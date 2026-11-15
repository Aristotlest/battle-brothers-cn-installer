"""Offline tests only: no real Steam endpoint, writes, restarts, or files."""
import importlib.util
import base64
import json
import hashlib
import http.server
import struct
import threading
import shutil
import subprocess
import unittest
from unittest.mock import Mock

from installer_core import SafetyError
from steam_ui import SteamUIUnavailable


class OptInTests(unittest.TestCase):
    def test_disabled_backend_never_starts_transport(self):
        spec = importlib.util.find_spec('steam_cdp')
        self.assertIsNotNone(spec, 'the opt-in CDP backend must exist')
        from steam_cdp import CdpSteamUI
        runner = Mock()
        backend = CdpSteamUI(runner=runner)
        with self.assertRaises(SteamUIUnavailable):
            backend.read()
        with self.assertRaises(SafetyError) as raised:
            backend.exchange('', 'new')
        self.assertNotIsInstance(raised.exception, SteamUIUnavailable)
        runner.assert_not_called()


    def test_only_literal_true_enables_backend(self):
        from steam_cdp import CdpSteamUI
        runner = Mock()
        for enabled in (1, 'yes', None, [], False):
            with self.subTest(enabled=enabled):
                with self.assertRaises(SteamUIUnavailable):
                    CdpSteamUI(enabled=enabled, runner=runner).read()
        runner.assert_not_called()


class ReadTests(unittest.TestCase):
    def test_selected_port_is_used_by_listener_discovery_and_websocket_guard(self):
        from steam_cdp import CdpSteamUI
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, b'{"value":"original"}', b''))
        self.assertEqual(CdpSteamUI(enabled=True, runner=runner, port=12691).read(), 'original')
        script = base64.b64decode(runner.call_args.args[0][-1]).decode('utf-16-le')
        self.assertIn('-LocalPort 12691', script)
        self.assertIn('http://127.0.0.1:12691/json/list', script)
        self.assertIn('ws://127.0.0.1:12691/devtools/page/', script)
        self.assertNotIn('8080', script)

    def test_enabled_read_uses_fixed_encoded_helper_and_json_stdin(self):
        from steam_cdp import CdpSteamUI, SCRIPT
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, b'{"value":"original"}', b''))
        self.assertEqual(CdpSteamUI(enabled=True, runner=runner).read(), 'original')
        args, kwargs = runner.call_args
        command = args[0]
        self.assertTrue(command[0].lower().endswith('powershell.exe'))
        self.assertIn('-NonInteractive', command)
        self.assertEqual(base64.b64decode(command[-1]).decode('utf-16-le'), SCRIPT)
        request = json.loads(kwargs['input'])
        self.assertEqual(set(request), {'expression'})
        self.assertIn('RegisterForAppDetails(365360', request['expression'])
        self.assertNotIn('appDetailsStore', request['expression'])
        self.assertTrue(0 < kwargs['timeout'] <= 30)
        self.assertNotIn('shell', kwargs)


class ExchangeTests(unittest.TestCase):
    def test_exchange_requires_fresh_reconnected_readback(self):
        from steam_cdp import CdpSteamUI
        runner = Mock(side_effect=[subprocess.CompletedProcess([], 0, b'{"submitted":true}', b''),
                                   subprocess.CompletedProcess([], 0, b'{"value":"new"}', b'')])
        self.assertEqual(CdpSteamUI(enabled=True, runner=runner).exchange('old', 'new'), 'new')
        self.assertEqual(runner.call_count, 2)
        first, second = [json.loads(call.kwargs['input'])['expression'] for call in runner.call_args_list]
        self.assertIn('SetAppLaunchOptions', first)
        self.assertNotIn('SetAppLaunchOptions', second)
        self.assertIn('RegisterForAppDetails', second)


class FailureTests(unittest.TestCase):
    def test_read_transport_errors_are_unavailable_but_exchange_never_is(self):
        from steam_cdp import CdpSteamUI
        failures = [OSError('missing helper'), subprocess.TimeoutExpired('helper', 20),
                    subprocess.CompletedProcess([], 2, b'{"error":"unavailable"}', b''),
                    subprocess.CompletedProcess([], 0, b'not json', b''),
                    subprocess.CompletedProcess([], 0, b'[]', b''),
                    subprocess.CompletedProcess([], 0, b'{"value":4}', b''),
                    subprocess.CompletedProcess([], 0, b'{"submitted":false}', b'')]
        for failure in failures:
            for operation in ('read', 'exchange'):
                with self.subTest(failure=failure, operation=operation):
                    runner = Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=failure)
                    backend = CdpSteamUI(enabled=True, runner=runner)
                    if operation == 'read':
                        with self.assertRaises(SteamUIUnavailable):
                            backend.read()
                    else:
                        with self.assertRaises(SafetyError) as raised:
                            backend.exchange('old', 'new')
                        self.assertNotIsInstance(raised.exception, SteamUIUnavailable)
                    self.assertEqual(runner.call_count, 1)

    def test_readback_mismatch_or_unavailability_is_terminal_write_failure(self):
        from steam_cdp import CdpSteamUI
        for readback in (b'{"value":"third-party"}', b'{"error":"unavailable"}'):
            runner = Mock(side_effect=[subprocess.CompletedProcess([], 0, b'{"submitted":true}', b''),
                                       subprocess.CompletedProcess([], 0, readback, b'')])
            with self.assertRaises(SafetyError) as raised:
                CdpSteamUI(enabled=True, runner=runner).exchange('old', 'new')
            self.assertNotIsInstance(raised.exception, SteamUIUnavailable)
            self.assertEqual(runner.call_count, 2)

    def test_invalid_expected_or_value_refused_before_helper(self):
        from steam_cdp import CdpSteamUI
        runner = Mock()
        for invalid in (None, 1, False, 'bad\nvalue', '\r', '\0', 'x' * 32769):
            for expected, value in ((invalid, 'valid'), ('valid', invalid)):
                with self.assertRaises(SafetyError):
                    CdpSteamUI(enabled=True, runner=runner).exchange(expected, value)
        runner.assert_not_called()


@unittest.skipUnless(shutil.which('node'), 'Node is used only by the offline JavaScript harness')
class SemanticTests(unittest.TestCase):
    def evaluate(self, expression, *, details=None, synchronous=False, setter_error=False,
                 registration_error=False, navigation: bool | str=False, unregister_error=False,
                 missing_handle=False):
        details = [{'unAppID': 365360, 'strLaunchOptions': 'original'}] if details is None else details
        source = '''
const cfg = JSON.parse(process.argv[1]);
let writes=[], unregisters=0, timers=new Map(), serial=0;
global.location={href:'https://steamloopback.host/index.html?SESSION=1'};
global.setTimeout=(fn,ms)=>{let id=++serial;timers.set(id,fn);return id;};
global.clearTimeout=id=>timers.delete(id);
global.SteamClient={Apps:{
 RegisterForAppDetails(appid,cb){
  if(cfg.registration_error)throw Error('register failed');
  const run=()=>{if(cfg.navigation)location.href=typeof cfg.navigation==='string'?cfg.navigation:'https://evil.invalid/';for(const d of cfg.details)cb(d);};
  if(cfg.synchronous)run();else queueMicrotask(run);
  if(cfg.missing_handle)return null;
  return {unregister(){unregisters++;if(cfg.unregister_error)throw Error('cleanup failed');}};
 },
 SetAppLaunchOptions(appid,value){writes.push([appid,value]);if(cfg.setter_error)throw Error('setter failed');}
}};
(async()=>{let value,error;try{let p=eval(cfg.expression);queueMicrotask(()=>{for(const fn of [...timers.values()])fn();});value=await p;}catch(e){error=String(e.message);}
await Promise.resolve();console.log(JSON.stringify({value,error,writes,unregisters,timers:timers.size}));})();
'''
        cfg = dict(expression=expression, details=details, synchronous=synchronous,
                   setter_error=setter_error, registration_error=registration_error, navigation=navigation,
                   unregister_error=unregister_error, missing_handle=missing_handle)
        result = subprocess.run([shutil.which('node') or 'node', '-e', source, json.dumps(cfg)],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return json.loads(result.stdout)

    def test_fresh_read_cleans_up_synchronous_and_asynchronous_subscriptions(self):
        from steam_cdp import READ
        for synchronous in (False, True):
            with self.subTest(synchronous=synchronous):
                result = self.evaluate(READ, synchronous=synchronous)
                self.assertEqual(result.get('value'), {'value': 'original'})
                self.assertEqual(result['unregisters'], 1)
                self.assertEqual(result['timers'], 0)
                self.assertEqual(result['writes'], [])
                self.assertNotIn('error', result)


    def test_malformed_details_navigation_and_timeout_fail_closed(self):
        from steam_cdp import READ
        for details in ([None], [{}], [{'unAppID': '365360', 'strLaunchOptions': ''}],
                        [{'unAppID': 1, 'strLaunchOptions': ''}],
                        [{'unAppID': 365360, 'strLaunchOptions': 1}],
                        [{'unAppID': 365360, 'strLaunchOptions': 'bad\nvalue'}], []):
            with self.subTest(details=details):
                result = self.evaluate(READ, details=details)
                self.assertIn('error', result)
                self.assertEqual(result['writes'], [])
                self.assertEqual(result['unregisters'], 1)
                self.assertEqual(result['timers'], 0)
        result = self.evaluate(READ, navigation=True)
        self.assertIn('error', result)
        self.assertEqual(result['writes'], [])

    def test_registration_error_clears_timer(self):
        from steam_cdp import READ
        result = self.evaluate(READ, registration_error=True)
        self.assertIn('error', result)
        self.assertEqual(result['timers'], 0)


    def test_same_host_navigation_before_callback_refuses_setter(self):
        from steam_cdp import exchange_expression
        result = self.evaluate(exchange_expression('original', 'new'),
                               navigation='https://steamloopback.host/index.html?SESSION=2')
        self.assertIn('error', result)
        self.assertEqual(result['writes'], [])
        self.assertEqual(result['unregisters'], 1)

    def test_duplicate_native_callbacks_submit_only_once(self):
        from steam_cdp import exchange_expression
        detail = {'unAppID': 365360, 'strLaunchOptions': 'original'}
        result = self.evaluate(exchange_expression('original', 'new'), details=[detail, detail])
        self.assertEqual(result['writes'], [[365360, 'new']])
        self.assertEqual(result['unregisters'], 1)

    def test_cleanup_failure_and_missing_handle_cannot_claim_success(self):
        from steam_cdp import READ
        for synchronous in (False, True):
            for kwargs in ({'unregister_error': True}, {'missing_handle': True}):
                with self.subTest(synchronous=synchronous, kwargs=kwargs):
                    result = self.evaluate(READ, synchronous=synchronous, **kwargs)
                    self.assertIn('error', result)
                    self.assertEqual(result['timers'], 0)

    def test_exchange_compares_and_sets_in_same_callback_with_json_escaping(self):
        from steam_cdp import exchange_expression
        value = '"C:\\游戏\\bb.exe" %command% \';global.evil=true;//'
        for synchronous in (False, True):
            result = self.evaluate(exchange_expression('original', value), synchronous=synchronous)
            self.assertEqual(result.get('value'), {'submitted': True})
            self.assertEqual(result['writes'], [[365360, value]])
            self.assertEqual(result['unregisters'], 1)
            self.assertEqual(result['timers'], 0)
        mismatch = self.evaluate(exchange_expression('different', value))
        self.assertIn('error', mismatch)
        self.assertEqual(mismatch['writes'], [])
        failed = self.evaluate(exchange_expression('original', value), setter_error=True)
        self.assertIn('error', failed)
        self.assertEqual(failed['unregisters'], 1)
        self.assertEqual(failed['timers'], 0)


@unittest.skipUnless(shutil.which('powershell.exe'), 'Windows PowerShell transport')
class NativeTransportTests(unittest.TestCase):
    def run_script(self, script):
        result = subprocess.run([shutil.which('powershell.exe') or 'powershell.exe', '-NoProfile',
                                 '-NonInteractive', '-EncodedCommand',
                                 base64.b64encode(script.encode('utf-16-le')).decode()],
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        return result.stdout.decode('utf-8-sig').strip()

    def fixture_evaluate(self, reply=None, *, redirect=False, change_target=False,
                         websocket_redirect=False, stall=False):
        """Exercise real .NET networking only against this test's ephemeral port."""
        from steam_cdp import SCRIPT
        requests = []
        discovery_count = []
        reply = {'id': 1, 'result': {'result': {'type': 'object',
                 'value': {'value': 'offline-native-response'}}}} if reply is None else reply
        class Fixture(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path == '/json/list':
                    discovery_count.append(1)
                    if redirect:
                        self.send_response(302)
                        self.send_header('Location', '/forbidden-redirect')
                        self.end_headers()
                        return
                    target_url = 'https://steamloopback.host/index.html?OFFLINE=1'
                    if change_target and len(discovery_count) > 1:
                        target_url += '&CHANGED=1'
                    body = json.dumps([dict(id='FIXTURE', title='SharedJSContext', type='page',
                        url=target_url,
                        webSocketDebuggerUrl=f'ws://127.0.0.1:{self.server.server_address[1]}/devtools/page/FIXTURE')]).encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path != '/devtools/page/FIXTURE':
                    self.send_error(404)
                    return
                if websocket_redirect:
                    self.send_response(302)
                    self.send_header('Location', '/forbidden-redirect')
                    self.end_headers()
                    return
                accept = base64.b64encode(hashlib.sha1((self.headers['Sec-WebSocket-Key'] +
                    '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                self.send_response(101)
                self.send_header('Upgrade', 'websocket')
                self.send_header('Connection', 'Upgrade')
                self.send_header('Sec-WebSocket-Accept', accept)
                self.end_headers()
                self.connection.settimeout(5)
                def exact(n):
                    value = b''
                    while len(value) < n:
                        chunk = self.rfile.read(n - len(value))
                        if not chunk:
                            raise EOFError()
                        value += chunk
                    return value
                if change_target:
                    return
                header = exact(2)
                size = header[1] & 127
                if size == 126:
                    size = struct.unpack('!H', exact(2))[0]
                elif size == 127:
                    size = struct.unpack('!Q', exact(8))[0]
                mask = exact(4)
                payload = exact(size)
                requests.append(json.loads(bytes(x ^ mask[i % 4] for i, x in enumerate(payload))))
                if stall:
                    try:
                        self.connection.recv(1)
                    except OSError:
                        pass
                    return
                payload = json.dumps(reply).encode()
                # Fragmented WebSocket text exercises the .NET receive loop.
                for opcode, fragment in ((1, payload[:20]), (128, payload[20:])):
                    length = len(fragment)
                    header = bytes([opcode, length]) if length < 126 else bytes([opcode, 126]) + struct.pack('!H', length)
                    self.wfile.write(header + fragment)
                    self.wfile.flush()
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            script = SCRIPT.replace('8080', str(server.server_port))
            if stall:
                script = script.replace('CancelAfter(12000)', 'CancelAfter(2000)')
            script = ("function Get-NetTCPConnection { [pscustomobject]@{LocalAddress='127.0.0.1';OwningProcess=123} }\n"
                      "function Get-Process { [pscustomobject]@{ProcessName='steamwebhelper';Path='C:\\Steam\\bin\\cef\\cef.win64\\steamwebhelper.exe'} }\n"
                      "function Get-ItemProperty { [pscustomobject]@{SteamPath='C:\\Steam'} }\n" + script)
            result = subprocess.run([shutil.which('powershell.exe') or 'powershell.exe', '-NoProfile',
                '-NonInteractive', '-EncodedCommand', base64.b64encode(script.encode('utf-16-le')).decode()],
                input=b'{"expression":"Promise.resolve({value: \'fixture\'})"}', capture_output=True, timeout=20)
            return result, requests
        finally:
            server.shutdown()
            server.server_close()
            worker.join(5)

    def test_real_dotnet_transport_against_offline_http_websocket_fixture(self):
        result, requests = self.fixture_evaluate()
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual(json.loads(result.stdout), {'value': 'offline-native-response'})
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['method'], 'Runtime.evaluate')
        self.assertTrue(requests[0]['params']['awaitPromise'])
        self.assertIn('location.href', requests[0]['params']['expression'])
        self.assertIn('https://steamloopback.host/index.html?OFFLINE=1', requests[0]['params']['expression'])

    def test_discovery_redirect_and_target_change_refuse_before_evaluation(self):
        for kwargs in ({'redirect': True}, {'change_target': True}):
            with self.subTest(kwargs=kwargs):
                result, requests = self.fixture_evaluate(**kwargs)
                self.assertEqual(result.returncode, 2)
                self.assertIn('error', json.loads(result.stdout))
                self.assertEqual(requests, [])

    def test_websocket_redirect_and_receive_timeout_fail_closed(self):
        for kwargs in ({'websocket_redirect': True}, {'stall': True}):
            with self.subTest(kwargs=kwargs):
                result, _ = self.fixture_evaluate(**kwargs)
                self.assertEqual(result.returncode, 2)
                self.assertIn('error', json.loads(result.stdout))

    def test_protocol_and_javascript_errors_fail_closed(self):
        for reply in ({'id': 1, 'error': {'message': 'bad'}},
                      {'id': 1, 'result': {'exceptionDetails': {'text': 'bad'}}},
                      {'id': 1, 'result': {'result': {'type': 'undefined'}}}):
            with self.subTest(reply=reply):
                result, _ = self.fixture_evaluate(reply)
                self.assertEqual(result.returncode, 2)
                self.assertIn('error', json.loads(result.stdout))

    def test_target_identity_rejects_lookalikes_ambiguity_and_websocket_escapes(self):
        from steam_cdp import SCRIPT
        definitions = SCRIPT.split('# BEGIN MAIN')[0]
        harness = r'''
$good=[pscustomobject]@{id='ABC-123';title='SharedJSContext';type='page';url='https://steamloopback.host/index.html?SESSION=1';webSocketDebuggerUrl='ws://127.0.0.1:8080/devtools/page/ABC-123'}
if((Select-Target @($good)).id -cne 'ABC-123') { throw 'valid target rejected' }
$cases=@(
 @(), @($good,$good),
 @([pscustomobject]@{id='ABC-123';title='Other';type='page';url=$good.url;webSocketDebuggerUrl=$good.webSocketDebuggerUrl})
)
foreach($url in @('http://steamloopback.host/index.html','https://steamloopback.host.evil/index.html','https://steamloopback.host:443/index.html','https://steamloopback.host/index.html/','https://steamloopback.host/index.html#x')) {
 $cases+=,@([pscustomobject]@{id=$good.id;title=$good.title;type='page';url=$url;webSocketDebuggerUrl=$good.webSocketDebuggerUrl})
}
foreach($ws in @('ws://evil:8080/devtools/page/ABC-123','ws://127.0.0.1:9999/devtools/page/ABC-123','ws://127.0.0.1:8080/devtools/page/other','ws://127.0.0.1:8080/devtools/page/ABC-123?x','ws://127.0.0.1:8080/devtools/page/../ABC-123')) {
 $cases+=,@([pscustomobject]@{id=$good.id;title=$good.title;type='page';url=$good.url;webSocketDebuggerUrl=$ws})
}
$cases+=,@([pscustomobject]@{id='ABC-123';title=$good.title;type='page';url=($good.url+"`n");webSocketDebuggerUrl=$good.webSocketDebuggerUrl})
$cases+=,@([pscustomobject]@{id="ABC-123`n";title=$good.title;type='page';url=$good.url;webSocketDebuggerUrl=($good.webSocketDebuggerUrl+"`n")})
foreach($case in $cases) {
 $accepted=$false; try { $null=Select-Target $case; $accepted=$true } catch {}
 if($accepted) { throw 'unsafe target accepted' }
}
'passed'
'''
        self.assertEqual(self.run_script(definitions + harness), 'passed')

    def test_listener_rejects_process_name_with_foreign_executable(self):
        from steam_cdp import SCRIPT
        definitions = SCRIPT.split('# BEGIN MAIN')[0]
        harness = r'''
function Get-NetTCPConnection { [pscustomobject]@{LocalAddress='127.0.0.1';OwningProcess=123} }
function Get-ItemProperty { [pscustomobject]@{SteamPath='C:\Steam'} }
function Get-Process { [pscustomobject]@{ProcessName='steamwebhelper';Path='C:\Unrelated\steamwebhelper.exe'} }
$accepted=$false
try { Assert-Listener; $accepted=$true } catch {}
if($accepted) { throw 'Accepted same-name foreign executable' }
'passed'
'''
        self.assertEqual(self.run_script(definitions + harness), 'passed')

    def test_listener_requires_exclusively_loopback_steamwebhelper_owner(self):
        from steam_cdp import SCRIPT
        definitions = SCRIPT.split('# BEGIN MAIN')[0]
        harness = r'''
function Get-NetTCPConnection { return $script:connections }
function Get-Process { return [pscustomobject]@{ProcessName=$script:owner;Path='C:\Steam\bin\cef\cef.win64\steamwebhelper.exe'} }
function Get-ItemProperty { [pscustomobject]@{SteamPath='C:\Steam'} }
$accepted=0; $rejected=0
foreach($address in @('127.0.0.1','::1','0.0.0.0','::','192.0.2.1')) {
 foreach($name in @('steamwebhelper','other')) {
  $script:connections=@([pscustomobject]@{LocalAddress=$address;OwningProcess=123})
  $script:owner=$name
  try { Assert-Listener; $accepted++ } catch { $rejected++ }
 }
}
if($accepted -ne 2 -or $rejected -ne 8) { throw "listener guard $accepted/$rejected" }
$script:connections=@(); try { Assert-Listener; throw 'accepted missing listener' } catch {
 if($_.Exception.Message -eq 'accepted missing listener') { throw }
}
'passed'
'''
        self.assertEqual(self.run_script(definitions + harness), 'passed')


if __name__ == '__main__':
    unittest.main()
