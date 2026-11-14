"""Fixed AppID Windows UI Automation bridge; values are JSON stdin, never code.

Steam's Chromium accessibility must expose an unambiguous launch-options edit.
Unknown/localized/ambiguous layouts fail closed. No pixel guesses or keystrokes.
"""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time
from installer_core import SafetyError
from steam_options import MANUAL, valid_text


class SteamUIUnavailable(SafetyError):
    """The semantic UIA route is unavailable before any write."""


# Embed the helper so frozen builds need no external script or writable script file.
SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if ($request.action -eq 'selftest') {
        @{value='ready'} | ConvertTo-Json -Compress
        exit 0
    }
    if ($request.action -notin @('read', 'exchange')) { throw 'Unknown action' }
    $AE = [System.Windows.Automation.AutomationElement]
    $TS = [System.Windows.Automation.TreeScope]
    $CT = [System.Windows.Automation.ControlType]
    function Semantic($node, $kind) {
        $id = $node.Current.AutomationId
        $name = $node.Current.Name.Trim()
        if ($kind -eq 'page') {
            # A title alone cannot identify an AppID. Require a provider-exposed
            # property-page identifier; unknown Steam layouts fail closed.
            $identity = $id + ' ' + $node.Current.HelpText
            return ($identity -match '(?i)(gameproperties[/_:= -]*365360(?![0-9])|app(?:id)?[/_:= -]*365360(?![0-9]).*propert|propert.*app(?:id)?[/_:= -]*365360(?![0-9]))')
        }
        if ($kind -eq 'general') {
            return ($id -match '(?i)(^|[-_:/])general($|[-_:/])' -or $name -match '^(?i:general|常规|一般|常規)$')
        }
        return ($id -match '(?i)launch[-_]?options' -or $name -match '^(?i:launch options|启动选项|啟動選項)$')
    }
    function Find-Field {
        $windows = $AE::RootElement.FindAll($TS::Children, [System.Windows.Automation.Condition]::TrueCondition)
        $matches = @()
        foreach ($window in $windows) {
            try {
                $process = Get-Process -Id $window.Current.ProcessId -ErrorAction Stop
                if ($process.ProcessName -notin @('steam', 'steamwebhelper')) { continue }
                $nodes = $window.FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
                $pages = @(@($window) + @($nodes) | Where-Object { Semantic $_ 'page' })
                foreach ($page in $pages) {
                    $controls = $page.FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
                    $tabs = @($controls | Where-Object { (Semantic $_ 'general') -and -not $_.Current.IsOffscreen -and $_.Current.IsEnabled })
                    if ($tabs.Count -ne 1) { continue }
                    $selection = $null
                    if (-not $tabs[0].TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$selection)) { continue }
                    if (-not $selection.Current.IsSelected) {
                        $selection.Select()
                        # Recapture all controls after navigation on the next poll.
                        continue
                    }
                    $edits = @()
                    foreach ($node in $controls) {
                        if ($node.Current.ControlType -ne $CT::Edit -or $node.Current.IsOffscreen -or -not $node.Current.IsEnabled -or $node.Current.IsPassword) { continue }
                        $label = $node.Current.LabeledBy
                        $identified = Semantic $node 'launch'
                        if (-not $identified -and $null -ne $label) { $identified = Semantic $label 'launch' }
                        if (-not $identified) { continue }
                        $pattern = $null
                        if (-not $node.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) { continue }
                        if ($pattern.Current.IsReadOnly) { continue }
                        $edits += ,@($node, $pattern)
                    }
                    if ($edits.Count -eq 1) { $matches += ,@($window, $edits[0][0], $edits[0][1], $page) }
                }
            } catch { continue }
        }
        if ($matches.Count -ne 1) { return $null }
        return ,$matches[0]
    }
    function Open-Field {
        if (-not (Get-Process -Name steam -ErrorAction SilentlyContinue)) { throw 'Steam is not running; no write attempted' }
        # Do not open the properties popup when the current Steam CEF build
        # exposes only four native wrapper nodes. That layout cannot be
        # navigated semantically and opening it would look like a false start.
        $existingSteam = $AE::RootElement.FindAll($TS::Children, [System.Windows.Automation.Condition]::TrueCondition)
        $accessible = $false
        foreach ($window in $existingSteam) {
            try {
                $process = Get-Process -Id $window.Current.ProcessId -ErrorAction Stop
                if ($process.ProcessName -in @('steam', 'steamwebhelper')) {
                    $nodes = $window.FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
                    if ($nodes.Count -gt 4) { $accessible = $true; break }
                }
            } catch { continue }
        }
        if (-not $accessible) {
            throw 'Steam 当前版本未提供网页控件无障碍树，无法准确导航到启动选项；未打开属性窗口、未写入。请手动打开 战场兄弟 → 通用 → 启动选项'
        }
        Start-Process 'steam://gameproperties/365360'
        $sawSteamWindow = $false
        $sawAccessibleSteamTree = $false
        for ($i=0; $i -lt 30; $i++) {
            Start-Sleep -Milliseconds 250
            $windows = $AE::RootElement.FindAll($TS::Children, [System.Windows.Automation.Condition]::TrueCondition)
            foreach ($window in $windows) {
                try {
                    $process = Get-Process -Id $window.Current.ProcessId -ErrorAction Stop
                    if ($process.ProcessName -notin @('steam', 'steamwebhelper')) { continue }
                    $sawSteamWindow = $true
                    $nodes = $window.FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
                    if ($nodes.Count -gt 4) { $sawAccessibleSteamTree = $true }
                } catch { continue }
            }
            $found = Find-Field
            if ($null -ne $found) { return ,$found }
        }
        if ($sawSteamWindow -and -not $sawAccessibleSteamTree) {
            throw 'Steam 当前版本未提供网页控件无障碍树，无法准确定位启动选项输入框；未写入。请手动打开 战场兄弟 → 通用 → 启动选项'
        }
        throw 'Unable to locate verified AppID 365360 properties, General tab and labeled launch-options input; no write attempted'
    }
    function Find-Close($field) {
        $buttons = $field[3].FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
        $found = @()
        foreach ($button in $buttons) {
            if ($button.Current.ControlType -ne $CT::Button -or $button.Current.IsOffscreen -or -not $button.Current.IsEnabled) { continue }
            if ($button.Current.AutomationId -notmatch '^(?i:(?:properties[-_]?)?close(?:[-_]?button)?)$' -and $button.Current.Name -notmatch '^(?i:close|close properties|关闭|關閉)$') { continue }
            $invoke = $null
            if ($button.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$invoke)) { $found += ,$invoke }
        }
        if ($found.Count -ne 1) { throw 'No unique semantic close control inside verified properties page; refusing write' }
        return $found[0]
    }
    function Close-Properties($field) {
        # Invoke the page's own close control, never close a top-level Steam window.
        $close = Find-Close $field
        $close.Invoke()
        for ($i=0; $i -lt 12; $i++) {
            Start-Sleep -Milliseconds 250
            try {
                if ($field[3].Current.IsOffscreen) { return }
                $probe = $field[3].FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
                if ($probe.Count -eq 0) { return }
            } catch [System.Windows.Automation.ElementNotAvailableException] { return }
        }
        throw 'Properties did not close; saved value not verified'
    }
    $field = Open-Field
    $current = [string]$field[2].Current.Value
    if ($request.action -eq 'read') {
        # Read means a saved/reopened value, not just an uncommitted edit buffer.
        Close-Properties $field
        $field = Open-Field
        $current = [string]$field[2].Current.Value
    }
    if ($request.action -eq 'exchange') {
        if ($request.expected -isnot [string] -or $request.value -isnot [string]) { throw 'Expected string values' }
        if ($request.value.Length -gt 32768 -or $request.value -match "[`r`n`0]") { throw 'Unsafe launch value' }
        # Establish close/reopen support before any write.
        $close = Find-Close $field
        # Revalidate page identity and value immediately before the setter.
        if (-not (Semantic $field[3] 'page')) { throw 'Properties identity changed; no write attempted' }
        $current = [string]$field[2].Current.Value
        if ($current -cne $request.expected) { throw 'Launch options changed concurrently; refusing write' }
        $field[2].SetValue([string]$request.value)
        Start-Sleep -Milliseconds 400
        if ([string]$field[2].Current.Value -cne $request.value) { throw 'UI readback mismatch' }
        Close-Properties $field
        $field = Open-Field
        $current = [string]$field[2].Current.Value
        if ($current -cne $request.value) { throw 'Saved launch-option readback mismatch' }
    }
    @{value=$current} | ConvertTo-Json -Compress
    exit 0
} catch {
    @{error=$_.Exception.Message} | ConvertTo-Json -Compress
    exit 2
}
'''




def is_target_window(title, class_name, width, height):
    """Require the observed Battle Brothers properties popup, not main Steam."""
    return (title.strip().casefold() == '战场兄弟 battle brothers'
            and class_name == 'SDL_app' and 600 <= width <= 1800 and 450 <= height <= 1400)


def field_point(width, height):
    """DPI-independent point in the verified General launch-options editor."""
    # The field is the wide right-hand pane: about 62% from the popup's
    # left edge and 76% down in the observed 1263x903 client rectangle.
    return (round(width * 0.62), round(height * 0.76))


class Win32SteamUI:
    """Conservative fallback for Steam CEF: verified popup + clipboard readback.

    CEF does not expose its DOM to UIA. The fallback therefore uses only the
    exact observed popup identity and a relative point in the General page,
    then verifies the actual field through clipboard copy before every write
    and after reopening the popup. It never touches localconfig.vdf.
    """
    WM_CLOSE = 0x0010
    VK_CONTROL = 0x11
    VK_A = 0x41
    VK_C = 0x43
    VK_V = 0x56
    KEYEVENTF_KEYUP = 0x0002
    SW_RESTORE = 9
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    def __init__(self, sleeper=time.sleep):
        self.sleeper = sleeper
        self.user32 = ctypes.windll.user32 if os.name == 'nt' else None
        self.kernel32 = ctypes.windll.kernel32 if os.name == 'nt' else None
        if self.user32 is not None:
            self.user32.OpenClipboard.argtypes = [wintypes.HWND]
            self.user32.OpenClipboard.restype = wintypes.BOOL
            self.user32.CloseClipboard.argtypes = []
            self.user32.CloseClipboard.restype = wintypes.BOOL
            self.user32.GetClipboardData.argtypes = [wintypes.UINT]
            self.user32.GetClipboardData.restype = wintypes.HANDLE
            self.user32.EmptyClipboard.argtypes = []
            self.user32.EmptyClipboard.restype = wintypes.BOOL
            self.user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
            self.user32.SetClipboardData.restype = wintypes.HANDLE
            self.user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
        if self.kernel32 is not None:
            self.kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
            self.kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
            self.kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
            self.kernel32.GlobalLock.restype = wintypes.LPVOID
            self.kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
            self.kernel32.GlobalUnlock.restype = wintypes.BOOL
            self.kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
            self.kernel32.GlobalFree.restype = wintypes.HGLOBAL

    def _windows(self):
        if self.user32 is None:
            return []
        result = []
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _):
            if not self.user32.IsWindowVisible(hwnd): return True
            length = self.user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            self.user32.GetWindowTextW(hwnd, title, length + 1)
            cls = ctypes.create_unicode_buffer(256)
            self.user32.GetClassNameW(hwnd, cls, 256)
            rect = wintypes.RECT()
            if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)): return True
            width, height = rect.right - rect.left, rect.bottom - rect.top
            if is_target_window(title.value, cls.value, width, height):
                result.append((hwnd, rect.left, rect.top, width, height))
            return True
        self.user32.EnumWindows(enum_proc(callback), 0)
        return result

    def _target(self, open_popup=True):
        found = self._windows()
        if not found and open_popup:
            os.startfile('steam://gameproperties/365360')
            for _ in range(40):
                self.sleeper(0.25)
                found = self._windows()
                if found: break
        if len(found) != 1:
            raise SteamUIUnavailable('未能唯一确认“战场兄弟 Battle Brothers”属性窗口，未写入。' + MANUAL)
        hwnd, left, top, width, height = found[0]
        return hwnd, left, top, width, height

    def _focus(self, hwnd):
        self.user32.ShowWindow(hwnd, self.SW_RESTORE)
        self.user32.SetForegroundWindow(hwnd)
        self.sleeper(0.15)

    def _key(self, key):
        self.user32.keybd_event(self.VK_CONTROL, 0, 0, 0)
        self.user32.keybd_event(key, 0, 0, 0)
        self.user32.keybd_event(key, 0, self.KEYEVENTF_KEYUP, 0)
        self.user32.keybd_event(self.VK_CONTROL, 0, self.KEYEVENTF_KEYUP, 0)

    def _clipboard(self, value=None):
        if self.user32 is None:
            raise SteamUIUnavailable('Windows 剪贴板不可用，未写入。' + MANUAL)
        if not self.user32.OpenClipboard(None):
            raise SteamUIUnavailable('无法打开 Windows 剪贴板，未写入。' + MANUAL)
        try:
            if value is None:
                handle = self.user32.GetClipboardData(self.CF_UNICODETEXT)
                if not handle:
                    raise SteamUIUnavailable('启动选项剪贴板回读不是 Unicode 文本，未写入。' + MANUAL)
                pointer = self.kernel32.GlobalLock(handle)
                if not pointer:
                    raise SteamUIUnavailable('无法锁定启动选项剪贴板文本，未写入。' + MANUAL)
                try:
                    return ctypes.wstring_at(pointer)
                finally:
                    self.kernel32.GlobalUnlock(handle)
            encoded = (value + '\x00').encode('utf-16-le')
            handle = self.kernel32.GlobalAlloc(self.GMEM_MOVEABLE, len(encoded))
            if not handle:
                raise SteamUIUnavailable('无法分配剪贴板文本，未写入。' + MANUAL)
            pointer = self.kernel32.GlobalLock(handle)
            if not pointer:
                self.kernel32.GlobalFree(handle)
                raise SteamUIUnavailable('无法锁定剪贴板文本，未写入。' + MANUAL)
            try:
                ctypes.memmove(pointer, encoded, len(encoded))
            finally:
                self.kernel32.GlobalUnlock(handle)
            if not self.user32.EmptyClipboard() or not self.user32.SetClipboardData(self.CF_UNICODETEXT, handle):
                self.kernel32.GlobalFree(handle)
                raise SteamUIUnavailable('无法写入 Windows 剪贴板，未写入。' + MANUAL)
        finally:
            self.user32.CloseClipboard()

    def _exchange_clipboard_safely(self, value, operation):
        previous = None
        try:
            try:
                previous = self._clipboard()
            except SteamUIUnavailable:
                previous = None
            self._clipboard(value)
            return operation()
        finally:
            if previous is not None:
                try:
                    self._clipboard(previous)
                except SteamUIUnavailable:
                    pass

    def _read_field(self, target):
        hwnd, left, top, width, height = target
        self._focus(hwnd)
        x, y = field_point(width, height)
        self.user32.SetCursorPos(left + x, top + y)
        self.user32.mouse_event(0x0002, 0, 0, 0, 0)
        self.user32.mouse_event(0x0004, 0, 0, 0, 0)
        self.sleeper(0.1)
        previous = None
        try:
            try:
                previous = self._clipboard()
            except SteamUIUnavailable:
                previous = None
            # A stale clipboard must never be accepted as a field readback.
            # Clear it before Ctrl+C and require the clipboard to become text.
            sentinel = '__BB_CN_READBACK_SENTINEL__'
            self._clipboard(sentinel)
            self._key(self.VK_A)
            self._key(self.VK_C)
            self.sleeper(0.1)
            value = self._clipboard()
            if value == sentinel:
                raise SteamUIUnavailable('启动选项输入框未响应复制，未写入。' + MANUAL)
        finally:
            if previous is not None:
                try:
                    self._clipboard(previous)
                except SteamUIUnavailable:
                    pass
        if not valid_text(value):
            raise SteamUIUnavailable('启动选项输入框未通过剪贴板回读验证，未写入。' + MANUAL)
        return value

    def read(self):
        target = self._target(True)
        return self._read_field(target)

    def exchange(self, expected, value):
        if not valid_text(expected) or not valid_text(value):
            raise SafetyError('Steam 启动选项值无效；拒绝修改。' + MANUAL)
        target = self._target(True)
        current = self._read_field(target)
        if current != expected:
            raise SafetyError('Steam 启动选项已变化，拒绝粘贴。' + MANUAL)
        hwnd, left, top, width, height = target
        x, y = field_point(width, height)
        def paste_and_verify():
            self.user32.SetCursorPos(left + x, top + y)
            self._key(self.VK_V)
            self.sleeper(0.3)
            if self._read_field(target) != value:
                raise SafetyError('自动粘贴后读回不匹配，未确认保存。' + MANUAL)
            self.user32.PostMessageW(hwnd, self.WM_CLOSE, 0, 0)
            self.sleeper(0.5)
            reopened = self._target(True)
            if self._read_field(reopened) != value:
                raise SafetyError('重新打开属性页后启动项不匹配。' + MANUAL)
            return value
        return self._exchange_clipboard_safely(value, paste_and_verify)


class AutoSteamUI:
    """Use semantic UIA first, then the verified CEF popup fallback."""
    def __init__(self, primary=None, fallback=None):
        self.primary = primary or WindowsSteamUI()
        self.fallback = fallback or Win32SteamUI()

    def read(self):
        try:
            return self.primary.read()
        except SteamUIUnavailable:
            return self.fallback.read()

    def exchange(self, expected, value):
        try:
            return self.primary.exchange(expected, value)
        except SteamUIUnavailable:
            return self.fallback.exchange(expected, value)


class WindowsSteamUI:
    def __init__(self, runner=subprocess.run):
        self.runner = runner

    def _request(self, payload):
        if os.name != 'nt':
            raise SafetyError('自动设置仅支持 Windows。' + MANUAL)
        executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        encoded = base64.b64encode(SCRIPT.encode('utf-16-le')).decode('ascii')
        try:
            result = self.runner([str(executable), '-NoLogo', '-NoProfile', '-NonInteractive', '-STA',
                                  '-EncodedCommand', encoded],
                                 input=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                 capture_output=True, timeout=60,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            output = result.stdout.decode('utf-8-sig') if isinstance(result.stdout, bytes) else result.stdout
            response = json.loads(output)
            if not isinstance(response, dict):
                raise ValueError('Invalid helper response object')
            if result.returncode or set(response) != {'value'} or not valid_text(response['value']):
                message = response.get('error', 'Invalid helper response')
                if any(marker in message for marker in (
                        '网页控件无障碍树', 'Unable to locate verified',
                        '未打开属性窗口', '未能准确导航')):
                    raise SteamUIUnavailable(message)
                raise SafetyError('Steam UI 自动化失败：' + message + '。' + MANUAL)
            return response['value']
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as exc:
            raise SafetyError('Steam UI 自动化失败：' + str(exc) + '。' + MANUAL) from exc

    def read(self):
        return self._request({'action': 'read'})

    def exchange(self, expected, value):
        if not valid_text(expected) or not valid_text(value):
            raise SafetyError('Steam 启动选项值无效；拒绝修改。' + MANUAL)
        return self._request({'action': 'exchange', 'expected': expected, 'value': value})
