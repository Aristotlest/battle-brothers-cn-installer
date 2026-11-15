"""Explicitly authorized, temporary Steam debugging; no Steam file writes."""
from contextlib import contextmanager
import base64
import json
import os
from pathlib import Path
import subprocess
import socket
import time
from installer_core import SafetyError, WindowsPlatform as CoreWindowsPlatform, atomic_json, no_links, parse_vdf


SNAPSHOT_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$processes = @(Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object {
    @{pid=[int]$_.ProcessId; parent_pid=[int]$_.ParentProcessId; name=$_.Name;
      exe=$_.ExecutablePath; command_line=$_.CommandLine; session_id=[int]$_.SessionId}
})
$listeners = @(Get-NetTCPConnection -ErrorAction Stop | Where-Object { $_.State -eq 'Listen' } | ForEach-Object {
    @{pid=[int]$_.OwningProcess; address=$_.LocalAddress; port=[int]$_.LocalPort}
})
@{processes=$processes; listeners=$listeners} | ConvertTo-Json -Depth 6 -Compress
'''


def _process_image(pid):
    """CIM omits some protected process paths even when limited-query works."""
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = w.DWORD(len(buffer))
        return buffer.value if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else None
    finally:
        kernel.CloseHandle(handle)


class WindowsPlatform(CoreWindowsPlatform):
    """Stdlib-only process adapter. Methods are injectable for offline testing."""
    def snapshot(self):
        script = base64.b64encode(SNAPSHOT_SCRIPT.encode('utf-16-le')).decode('ascii')
        executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        try:
            result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-EncodedCommand', script],
                                    capture_output=True, timeout=25,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise SafetyError('Could not inspect Windows processes and TCP listeners.')
            data = json.loads(result.stdout.decode('utf-8-sig'))
            if not isinstance(data, dict) or not isinstance(data.get('processes'), list) or not data['processes'] or not isinstance(data.get('listeners'), list):
                raise ValueError('Incomplete process snapshot')
            for p in data['processes']:
                if (not isinstance(p, dict) or type(p.get('pid')) is not int or p['pid'] < 0 or
                    type(p.get('parent_pid')) is not int or not isinstance(p.get('name'), str) or not p['name'] or
                    'exe' not in p or 'command_line' not in p or
                    p['exe'] is not None and not isinstance(p['exe'], str) or
                    p['command_line'] is not None and not isinstance(p['command_line'], str)):
                    raise ValueError('Invalid process identity')
            for s in data['listeners']:
                if (not isinstance(s, dict) or type(s.get('pid')) is not int or s['pid'] < 0 or
                    type(s.get('port')) is not int or not 1 <= s['port'] <= 65535 or not isinstance(s.get('address'), str)):
                    raise ValueError('Invalid listener identity')
            for p in data['processes']:
                if not p['exe']:
                    p['exe'] = _process_image(p['pid'])
            return data
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise SafetyError('Could not inspect Windows processes and TCP listeners safely.') from error

    def start(self, args):
        subprocess.Popen(list(args), shell=False, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def debug_port(self):
        # An actual exclusive bind detects Windows excluded ranges as well as
        # occupied sockets. Releasing it cannot eliminate races; readiness and
        # transport guards independently authenticate the eventual owner.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(('127.0.0.1', 0))
                return probe.getsockname()[1]
        except OSError as error:
            raise SafetyError('No usable loopback Steam debugging port; no restart attempted.') from error

    def monotonic(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)


def _same_path(left, right):
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _inside(path, root):
    try:
        return os.path.commonpath([os.path.normcase(os.path.abspath(path)),
                                   os.path.normcase(os.path.abspath(root))]) == os.path.normcase(os.path.abspath(root))
    except ValueError:
        return False


def _ordinary_command(command, exe):
    if not isinstance(command, str):
        return False
    candidate = command
    if candidate.startswith('"') and candidate.endswith('"'):
        candidate = candidate[1:-1]
    if '"' in candidate or not Path(candidate).is_absolute():
        return False
    return _same_path(candidate, exe)


def _libraries(steam):
    libraries = [steam]
    folders = steam / 'steamapps/libraryfolders.vdf'
    no_links(folders)
    if folders.exists():
        entries = parse_vdf(folders.read_text(encoding='utf-8-sig')).get('libraryfolders')
        if not isinstance(entries, dict):
            raise SafetyError('Invalid Steam library list.')
        for key, entry in entries.items():
            if not key.isdigit():
                continue
            value = entry.get('path') if isinstance(entry, dict) else entry
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise SafetyError('Invalid Steam library path.')
            library = Path(value)
            no_links(library)
            libraries.append(library)
    return libraries


def _process_exited(pid):
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: PID gone
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        kernel.CloseHandle(handle)


def _owned(snapshot, steam):
    owned = []
    for process in snapshot['processes']:
        if process['name'].casefold() not in ('steam.exe', 'steamwebhelper.exe'):
            continue
        if not process.get('exe') and _process_exited(process['pid']):
            continue
        if not process.get('exe') or not _inside(process['exe'], steam):
            raise SafetyError('Cannot establish Steam process installation ownership.')
        no_links(Path(process['exe']))
        owned.append(process)
    return owned


def _preflight(platform, steam, exe):
    no_links(exe)
    if not steam.is_absolute() or not exe.is_file():
        raise SafetyError('Steam executable must be an existing absolute regular file.')
    for relative in ('.cef-enable-remote-debugging', 'steam.cfg'):
        if os.path.lexists(steam / relative):
            raise SafetyError('Existing Steam debug marker/configuration; no restart attempted.')
    libraries = _libraries(steam)
    snapshot = platform.snapshot()
    processes = snapshot['processes']
    clients = [p for p in processes if p['name'].casefold() == 'steam.exe']
    if len(clients) != 1 or not clients[0].get('exe') or not _same_path(clients[0]['exe'], exe):
        raise SafetyError('Exactly one Steam client from the discovered installation must be running.')
    original = clients[0]
    command = original.get('command_line')
    # Deliberately accept only the exact absolute executable, optionally quoted.
    if not _ordinary_command(command, exe):
        raise SafetyError('Steam must have an ordinary no-argument launch; original arguments cannot be overridden.')
    known_games = {'battlebrothers.exe', 'bb-launcher-steam.exe', 'bb-launcher-gog.exe'}
    for p in processes:
        name = p['name'].casefold()
        path = p.get('exe')
        if not path and p.get('session_id') != 0 and name not in ('system idle process', 'system', 'registry', 'memory compression', 'secure system'):
            raise SafetyError('Cannot identify a process executable; cannot rule out a running Steam game.')
        if name in known_games or (path and any(_inside(path, lib / 'steamapps') for lib in libraries)):
            raise SafetyError('A Steam game or library executable is running; close all games first.')
        if name in ('steam.exe', 'steamwebhelper.exe'):
            if not path or not _inside(path, steam):
                raise SafetyError('Steam process belongs to another installation.')
            command_line = p.get('command_line')
            if not isinstance(command_line, str) or any(flag in command_line.casefold() for flag in ('remote-debugging-port', 'cef-enable-remote-debugging', 'cef-enable-debugging', 'devtools-port')):
                raise SafetyError('Pre-existing Steam remote debugging; no restart attempted.')
    helper_pids = {p['pid'] for p in _owned(snapshot, steam) if p['name'].casefold() == 'steamwebhelper.exe'}
    if any(s['pid'] in helper_pids or s['port'] == 8080 for s in snapshot['listeners']):
        raise SafetyError('Steam web helper already owns a TCP listener; refusing to hijack it.')
    return original


def _wait(platform, check, message, timeout=60):
    deadline = platform.monotonic() + timeout
    while True:
        result = check()
        if result:
            return result
        if platform.monotonic() >= deadline:
            raise SafetyError(message)
        platform.sleep(0.5)


def _debug_ready(platform, steam, backend, port=8080):
    snapshot = platform.snapshot()
    owned = _owned(snapshot, steam)
    helpers = {p['pid'] for p in owned if p['name'].casefold() == 'steamwebhelper.exe'}
    relevant = [s for s in snapshot['listeners'] if s['pid'] in helpers or s['port'] in (8080, port)]
    for socket in relevant:
        # CIM process enumeration precedes TCP enumeration. A newly started
        # helper can appear only in the latter; wait, never use that endpoint.
        if socket['address'] in ('127.0.0.1', '::1') and socket['pid'] not in {p['pid'] for p in snapshot['processes']}:
            return False
        if socket['pid'] not in helpers or socket['address'] not in ('127.0.0.1', '::1'):
            raise SafetyError('Debug listener is not loopback-only and owned by this Steam installation: ' + json.dumps({'listener': socket, 'owned_helpers': sorted(helpers)}))
    if not any(s['port'] == port for s in relevant):
        return False
    try:
        value = backend.read()
    except Exception:
        return False
    return isinstance(value, str)


def _normal_ready(platform, steam, exe, port=8080):
    snapshot = platform.snapshot()
    owned = _owned(snapshot, steam)
    clients = [p for p in owned if p['name'].casefold() == 'steam.exe' and _same_path(p['exe'], exe)]
    if len(clients) != 1:
        return False
    command = clients[0].get('command_line')
    if not _ordinary_command(command, exe):
        return False
    helpers = {p['pid'] for p in owned if p['name'].casefold() == 'steamwebhelper.exe'}
    if not helpers or any(s['pid'] in helpers or s['port'] in (8080, port) for s in snapshot['listeners']):
        return False
    return not any('remote-debugging-port' in (p.get('command_line') or '').casefold() or
                   'cef-enable-remote-debugging' in (p.get('command_line') or '').casefold() or
                   'cef-enable-debugging' in (p.get('command_line') or '').casefold() or
                   'devtools-port' in (p.get('command_line') or '').casefold() for p in owned)


def _shutdown(platform, steam, exe):
    platform.start([str(exe), '-shutdown'])
    _wait(platform, lambda: not _owned(platform.snapshot(), steam),
          'Steam graceful shutdown timed out; no force termination attempted.')


@contextmanager
def debug_session(*, consent=False, platform=None):
    """Yield a read-verified CdpSteamUI only with literal ``consent=True``.

    ``platform`` may inject steam(), snapshot(), start(argv), monotonic(), and
    sleep(seconds). The default uses the current user's Steam registry entry and
    bounded PowerShell process/TCP snapshots; no third-party packages are needed.
    Only an already-running, no-argument Steam launch is accepted. A probed
    loopback port is passed explicitly through launch, transport and cleanup.

    This controls the client lifecycle, not launch-option rollback. The caller
    must manage any backend exchange transaction. Every post-journal failure
    attempts graceful shutdown and a verified normal restart. If Steam cannot
    exit normally, no second instance is launched and recovery evidence remains.
    Stale evidence always needs manual recovery/archiving, never silent removal.
    """
    if consent is not True:
        raise SafetyError('Explicit true consent is required before accessing Steam.')
    platform = platform or WindowsPlatform()
    steam = Path(platform.steam())
    exe = steam / 'steam.exe'
    local = os.environ.get('LOCALAPPDATA')
    if not local or not Path(local).is_absolute():
        raise SafetyError('An absolute LOCALAPPDATA is required for durable recovery evidence.')
    journal = Path(local) / 'BB-CN-Installer/steam-debug-session.json'
    no_links(journal)
    if journal.exists():
        raise SafetyError('Unresolved Steam debug session: ' + str(journal) +
                          '. Do not delete it blindly. Exit Steam normally, confirm steam.exe and '
                          'steamwebhelper.exe are gone, restart the recorded steam_exe without arguments, '
                          'verify Steam is alive with no web-helper debug listeners, then archive the journal manually.')
    original = _preflight(platform, steam, exe)
    port = platform.debug_port()
    if type(port) is not int or not 1024 <= port <= 65535:
        raise SafetyError('Invalid probed debugging port; no restart attempted.')
    if any(s['port'] == port for s in platform.snapshot()['listeners']):
        raise SafetyError('Selected debugging port is occupied; no restart attempted.')
    debug_argv = [str(exe), '-cef-enable-debugging', '-devtools-port', str(port)]
    journal.parent.mkdir(parents=True, exist_ok=True)
    try:
        with journal.open('x', encoding='utf-8') as reservation:
            reservation.write('{"schema":1,"phase":"reserved-no-shutdown-yet"}')
            reservation.flush()
            os.fsync(reservation.fileno())
    except FileExistsError as error:
        raise SafetyError('Another or stale Steam session journal exists: ' + str(journal)) from error
    atomic_json(journal, {'schema': 1, 'phase': 'prepared-before-shutdown',
                          'steam_exe': str(exe), 'original_pid': original['pid'],
                          'original_command_line': original['command_line'],
                          'debug_argv': debug_argv, 'debug_port': port,
                          'restore_argv': [str(exe)],
                          'recovery': 'Exit Steam normally; wait for steam.exe and steamwebhelper.exe to exit. '
                                      'Start steam_exe without arguments. Verify no web-helper TCP listeners '
                                      'and normal Steam alive before manually archiving this journal.'})
    try:
        _shutdown(platform, steam, exe)
        platform.start(debug_argv)
        from steam_cdp import CdpSteamUI
        backend = CdpSteamUI(enabled=True, port=port)
        _wait(platform, lambda: _debug_ready(platform, steam, backend, port),
              'Steam debug backend read was not ready before timeout.')
        yield backend
    finally:
        try:
            _shutdown(platform, steam, exe)
            platform.start([str(exe)])
            _wait(platform, lambda: _normal_ready(platform, steam, exe, port),
                  'Normal Steam restart and debug-listener removal could not be verified.')
            journal.unlink()
        except BaseException as error:
            raise SafetyError('Steam restoration cleanup failed; recovery journal retained at ' + str(journal)) from error
