"""Explicit opt-in semantic Steam launch options, fixed AppID 365360.

Connects only to an existing, verified loopback Steam debugger; never launches,
restarts, or edits Steam files. Callers own consent, durable backups and recovery.
Steam's internal native API is version-dependent, not a supported public API.
"""
import base64
import json
import os
from pathlib import Path
import subprocess

from installer_core import SafetyError
from steam_options import valid_text
from steam_ui import SteamUIUnavailable


SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
function Assert-Listener {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction Stop)
    if ($listeners.Count -eq 0) { throw 'Steam debugger is not already listening' }
    foreach ($listener in $listeners) {
        if ($listener.LocalAddress -cnotin @('127.0.0.1', '::1')) { throw 'Debugger is not loopback-only' }
        $owner = Get-Process -Id $listener.OwningProcess -ErrorAction Stop
        if ($owner.ProcessName -cne 'steamwebhelper') { throw 'Debugger is not owned by steamwebhelper' }
        $root = (Get-ItemProperty -LiteralPath 'HKCU:\Software\Valve\Steam' -Name SteamPath -ErrorAction Stop).SteamPath
        if ($root -isnot [string] -or -not [IO.Path]::IsPathRooted($root) -or $owner.Path -isnot [string]) {
            throw 'Unknown Steam installation ownership'
        }
        $expected = [IO.Path]::GetFullPath((Join-Path $root 'bin\cef\cef.win64\steamwebhelper.exe'))
        if (-not [string]::Equals([IO.Path]::GetFullPath($owner.Path), $expected, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Debugger executable is outside the discovered Steam installation'
        }
    }
}
function Select-Target($targets) {
    $matches = @($targets | Where-Object {
        $_.title -ceq 'SharedJSContext' -and $_.type -ceq 'page' -and
        $_.url -is [string] -and $_.url -cmatch '\Ahttps://steamloopback\.host/index\.html(?:\?[^#\x00-\x20\x7f]*)?\z'
    })
    if ($matches.Count -ne 1) { throw 'No unique verified Steam SharedJSContext' }
    $target = $matches[0]
    if ($target.id -isnot [string] -or $target.id -cnotmatch '\A[A-Za-z0-9_-]+\z' -or
        $target.webSocketDebuggerUrl -cne ('ws://127.0.0.1:8080/devtools/page/' + $target.id)) {
        throw 'Unsafe debugger WebSocket target'
    }
    return $target
}
function Get-Targets {
    $http = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:8080/json/list')
    $http.Proxy = $null
    $http.AllowAutoRedirect = $false
    $http.Timeout = 2000
    $http.ReadWriteTimeout = 2000
    $response = $null; $reader = $null
    try {
        $response = $http.GetResponse()
        if ([int]$response.StatusCode -ne 200) { throw 'Debugger discovery HTTP status' }
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        $buffer = New-Object char[] 4096
        $text = New-Object System.Text.StringBuilder
        while (($count = $reader.Read($buffer, 0, $buffer.Length)) -gt 0) {
            if ($text.Length + $count -gt 1048576) { throw 'Oversized debugger discovery' }
            $null = $text.Append($buffer, 0, $count)
        }
        return ($text.ToString() | ConvertFrom-Json)
    } finally {
        if ($reader) { $reader.Dispose() }
        if ($response) { $response.Dispose() }
        $http.Abort()
    }
}
function Invoke-CDP($target, $expression) {
    $socket = New-Object System.Net.WebSockets.ClientWebSocket
    $socket.Options.Proxy = $null
    $cancel = New-Object System.Threading.CancellationTokenSource
    $cancel.CancelAfter(12000)
    try {
        $null = $socket.ConnectAsync([uri]$target.webSocketDebuggerUrl, $cancel.Token).GetAwaiter().GetResult()
        Assert-Listener
        $fresh = Select-Target @(Get-Targets)
        foreach ($key in @('id','title','type','url','webSocketDebuggerUrl')) {
            if ($fresh.$key -cne $target.$key) { throw 'Debugger target changed during connection' }
        }
        $location = ConvertTo-Json -InputObject $target.url -Compress
        $guarded = '(() => { if (location.href !== ' + $location + ') throw new Error("Steam location changed"); return (' + $expression + '); })()'
        $message = @{id=1;method='Runtime.evaluate';params=@{expression=$guarded;returnByValue=$true;awaitPromise=$true;timeout=9000}} | ConvertTo-Json -Depth 8 -Compress
        $bytes = [Text.Encoding]::UTF8.GetBytes($message)
        $segment = New-Object 'System.ArraySegment[byte]' -ArgumentList (,$bytes)
        $null = $socket.SendAsync($segment, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $cancel.Token).GetAwaiter().GetResult()
        $buffer = New-Object byte[] 8192
        $segment = New-Object 'System.ArraySegment[byte]' -ArgumentList (,$buffer)
        while ($true) {
            $stream = New-Object System.IO.MemoryStream
            try {
                do {
                    $part = $socket.ReceiveAsync($segment, $cancel.Token).GetAwaiter().GetResult()
                    if ($part.MessageType -ne [System.Net.WebSockets.WebSocketMessageType]::Text) { throw 'Non-text debugger response' }
                    if ($stream.Length + $part.Count -gt 1048576) { throw 'Oversized debugger response' }
                    $stream.Write($buffer, 0, $part.Count)
                } while (-not $part.EndOfMessage)
                $reply = [Text.Encoding]::UTF8.GetString($stream.ToArray()) | ConvertFrom-Json
            } finally { $stream.Dispose() }
            if ($reply.id -ne 1) { continue }
            if ($reply.error -or $reply.result.exceptionDetails -or
                $null -eq $reply.result.result.value) { throw 'Debugger evaluation failed' }
            return $reply.result.result.value
        }
    } finally {
        $socket.Abort()
        $socket.Dispose()
        $cancel.Dispose()
    }
}
# BEGIN MAIN
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if ($request.expression -isnot [string]) { throw 'Invalid CDP request' }
    Assert-Listener
    $target = Select-Target @(Get-Targets)
    Invoke-CDP $target $request.expression | ConvertTo-Json -Depth 8 -Compress
    exit 0
} catch {
    @{error='Steam CDP operation unavailable or unverified'} | ConvertTo-Json -Compress
    exit 2
}
'''
READ = r'''new Promise((resolve, reject) => {
    let handle = null, done = false, registrationError = null;
    const contextURL = location.href;
    function finish(error, value) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        // Defer only settlement/cleanup, NEVER the comparison or setter. The
        // native provider may call back before returning its subscription.
        queueMicrotask(() => {
            try {
                if (registrationError) throw registrationError;
                if (!handle || typeof handle.unregister !== 'function')
                    throw new Error('Missing native subscription handle');
                const h = handle; handle = null; h.unregister();
            } catch (e) { error = e; }
            if (error) reject(error); else resolve(value);
        });
    }
    const timer = setTimeout(() => finish(new Error('Native details timeout')), 8000);
    try {
        handle = SteamClient.Apps.RegisterForAppDetails(365360, d => {
            if (done) return;
            try {
                if (location.href !== contextURL ||
                    !/^https:\/\/steamloopback\.host\/index\.html(?:\?[^#]*)?$/.test(location.href))
                    throw new Error('Steam context identity changed');
                if (!d || d.unAppID !== 365360 || typeof d.strLaunchOptions !== 'string' ||
                    [...d.strLaunchOptions].length > 32768 || /[\u0000\r\n]/.test(d.strLaunchOptions))
                    throw new Error('Malformed native details');
                finish(null, {value: d.strLaunchOptions});
            } catch (e) { finish(e); }
        });
        if (!handle || typeof handle.unregister !== 'function')
            throw new Error('Missing native subscription handle');
    } catch (e) { registrationError = e; finish(e); }
})'''


def exchange_expression(expected, value):
    """The native callback compares and calls the setter without yielding."""
    if not valid_text(expected) or not valid_text(value):
        raise SafetyError('Invalid Steam launch options; refusing write.')
    action = ('if (d.strLaunchOptions !== ' + json.dumps(expected) + ') '
              'throw new Error("Launch options changed concurrently");\n'
              'SteamClient.Apps.SetAppLaunchOptions(365360, ' + json.dumps(value) + ');\n'
              'finish(null, {submitted: true});')
    return READ.replace('finish(null, {value: d.strLaunchOptions});', action)


class CdpSteamUI:
    """Disabled by default; read failures alone permit preflight fallback.

    Exchange either verifies a new native subscription on a new connection or
    raises SafetyError. It never retries a setter, falls back, or rolls back.
    """
    def __init__(self, *, enabled=False, runner=subprocess.run, port=8080):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise SafetyError('Invalid local Steam debugging port.')
        self.enabled = enabled is True
        self.runner = runner
        self.port = port

    def _evaluate(self, expression, *, submitted=False):
        executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        try:
            result = self.runner(
                [str(executable), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                 base64.b64encode(SCRIPT.replace('8080', str(self.port)).encode('utf-16-le')).decode('ascii')],
                input=json.dumps({'expression': expression}, ensure_ascii=True).encode('utf-8'),
                capture_output=True, timeout=20,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            response = json.loads(result.stdout)
            if result.returncode or not isinstance(response, dict):
                raise ValueError('Steam CDP helper failed')
            if submitted:
                if response != {'submitted': True} or type(response.get('submitted')) is not bool:
                    raise ValueError('Invalid setter acknowledgement')
            elif set(response) != {'value'} or not valid_text(response['value']):
                raise ValueError('Invalid native details response')
            return response
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as exc:
            raise SafetyError('Steam CDP operation failed; outcome not verified.') from exc

    def read(self):
        if not self.enabled:
            raise SteamUIUnavailable('Steam CDP requires explicit opt-in.')
        try:
            return self._evaluate(READ)['value']
        except SafetyError as exc:
            raise SteamUIUnavailable('Steam CDP native read unavailable; no write attempted.') from exc

    def exchange(self, expected, value):
        if not self.enabled:
            raise SafetyError('Steam CDP requires explicit opt-in; no write attempted.')
        self._evaluate(exchange_expression(expected, value), submitted=True)
        # Never call public read(): its unavailable type permits preflight fallback.
        observed = self._evaluate(READ)['value']
        if observed != value:
            raise SafetyError('Fresh native Steam readback mismatch; write outcome unverified.')
        return observed
