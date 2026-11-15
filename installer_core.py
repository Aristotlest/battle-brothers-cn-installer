"""Battle Brothers CN installer. No game data or third-party binaries included."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import os
import re
import zipfile
import subprocess
import urllib.request
import uuid
from contextlib import contextmanager
from functools import wraps

PINS = json.loads(Path(__file__).with_name('dependencies.json').read_text(encoding='utf-8'))
TARGETS = ('data/zdata_cn.zip', 'win32/bb-launcher-steam.exe')
BUNDLED_TEXTS = Path(__file__).with_name('resources') / 'translation-source.zip'


def translation_archive(cache):
    """Prefer exact unmodified embedded GPL source; corruption never falls back."""
    if BUNDLED_TEXTS.exists():
        no_links(BUNDLED_TEXTS)
        if sha256(BUNDLED_TEXTS) != PINS['texts']['sha256']:
            raise SafetyError('内置汉化文本校验失败；请重新获取安装器')
        return BUNDLED_TEXTS
    return fetch_pinned(PINS['texts'], Path(cache) / 'translation-source.zip')

class SafetyError(RuntimeError):
    """Refused an operation rather than risk game or user data."""

def sha256(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

@dataclass(frozen=True)
class Prepared:
    path: Path
    digest: str
    warnings: list

def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    with open(temp, 'w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)

def durable_copy(source, target):
    with open(source, 'rb') as src, open(target, 'wb') as dst:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())

def no_links(path):
    path = Path(path).absolute()
    for p in (path, *path.parents):
        try:
            s = p.lstat()
        except FileNotFoundError:
            continue
        if p.is_symlink() or getattr(s, 'st_file_attributes', 0) & 0x400:
            raise SafetyError('拒绝符号链接 / junction / reparse point: ' + str(p))
        if p.is_file() and s.st_nlink > 1:
            raise SafetyError('拒绝硬链接文件: ' + str(p))

def audit_zip(archive, max_bytes=2 * 1024**3):
    try:
        z = zipfile.ZipFile(archive)
        with z:
            infos = z.infolist()
            if not infos or len(infos) > 100000 or sum(i.file_size for i in infos) > max_bytes:
                raise SafetyError('ZIP 为空或超过大小 / 文件数限制')
            seen = {}
            reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
            for i in infos:
                name = i.filename
                parts = name.rstrip('/').split('/')
                is_dir = i.is_dir() or name.endswith('/')
                if (not name or name.startswith('/') or '\\' in name or ':' in name or
                    any(p in ('', '.', '..') or p.endswith((' ', '.')) or p.split('.')[0].upper() in reserved for p in parts) or
                    ((i.external_attr >> 16) & 0o170000) == 0o120000 or i.flag_bits & 1):
                    raise SafetyError('ZIP 包含不安全路径 / 链接: ' + name)
                key = '/'.join(parts).casefold()
                if key in seen: raise SafetyError('ZIP 路径重复（Windows 不区分大小写）')
                for ancestor in range(1, len(parts)):
                    ancestor_key = '/'.join(parts[:ancestor]).casefold()
                    if ancestor_key in seen and not seen[ancestor_key]:
                        raise SafetyError('ZIP 文件 / 目录冲突')
                if is_dir and any(existing.startswith(key + '/') for existing in seen):
                    raise SafetyError('ZIP 文件 / 目录冲突')
                seen[key] = is_dir
            if z.testzip() is not None: raise SafetyError('ZIP CRC 校验失败')
            return infos
    except (zipfile.BadZipFile, RuntimeError, OSError) as e:
        raise SafetyError('ZIP 校验失败') from e

def filesystem_path(path):
    """Use Win32 extended paths internally without a machine-wide policy change."""
    path = Path(path).absolute()
    if os.name == 'nt' and not str(path).startswith('\\\\?\\'):
        text = str(path)
        path = Path('\\\\?\\UNC\\' + text[2:] if text.startswith('\\\\') else '\\\\?\\' + text)
    return path


def safe_extract(archive, destination, max_bytes=2 * 1024**3):
    infos = audit_zip(archive, max_bytes)
    destination = filesystem_path(destination)
    no_links(destination)
    if destination.exists(): raise SafetyError('提取目录必须不存在')
    try:
        destination.mkdir(parents=True)
        with zipfile.ZipFile(archive) as z:
            for i in infos:
                target = destination.joinpath(*i.filename.rstrip('/').split('/'))
                if i.is_dir(): target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(i) as src, open(target, 'xb') as dst: shutil.copyfileobj(src, dst)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
def parse_vdf(text):
    tokens = []
    pos = 0
    pattern = re.compile(r'\s+|//[^\n]*|"((?:\\.|[^"\\])*)"|([{}])')
    while pos < len(text):
        m = pattern.match(text, pos)
        if not m: raise SafetyError('VDF 语法无效')
        pos = m.end()
        if m.group(1) is not None:
            tokens.append(re.sub(r'\\([\\"])', r'\1', m.group(1)))
        elif m.group(2): tokens.append(m.group(2))
    index = 0
    def obj(nested=False):
        nonlocal index
        result = {}
        while index < len(tokens):
            key = tokens[index]
            index += 1
            if key == '}':
                if not nested: raise SafetyError('VDF 多余括号')
                return result
            if key == '{' or index >= len(tokens): raise SafetyError('VDF 键值缺失')
            value = tokens[index]
            index += 1
            if value == '{': value = obj(True)
            elif value == '}': raise SafetyError('VDF 值缺失')
            if key.lower() in result: raise SafetyError('VDF 重复键')
            result[key.lower()] = value
        if nested: raise SafetyError('VDF 括号未关闭')
        return result
    return obj()

def discover_games(steam):
    steam = Path(steam).absolute()
    no_links(steam)
    no_links(steam / 'steamapps')
    libraries = [steam]
    folder = steam / 'steamapps/libraryfolders.vdf'
    if folder.is_file():
        root = parse_vdf(folder.read_text(encoding='utf-8-sig')).get('libraryfolders', {})
        for key, entry in root.items():
            if not key.isdigit():
                continue
            value = entry.get('path') if isinstance(entry, dict) else entry
            if not value:
                continue
            library = Path(value).absolute()
            try: library = library.resolve(strict=False)
            except OSError: continue
            no_links(library)
            libraries.append(library)
    games = []
    for lib in libraries:
        manifest = lib / 'steamapps/appmanifest_365360.acf'
        if manifest.is_file():
            app = parse_vdf(manifest.read_text(encoding='utf-8-sig')).get('appstate', {})
            name = app.get('installdir', '')
            if app.get('appid') == '365360' and name and not any(c in name for c in '/\\:') and name not in ('.', '..'):
                game = lib / 'steamapps/common' / name
                if game.is_dir() and game not in games: games.append(game)
    return games

def launch_option(game):
    path = str(Path(game).absolute() / TARGETS[1])
    if any(c in path for c in '\r\n"%'): raise SafetyError('游戏路径包含不安全启动选项字符')
    return '"' + path + '" %command%'

def fetch_pinned(pin, target):
    target = Path(target)
    no_links(target)
    if target.exists():
        if sha256(target) != pin['sha256']: raise SafetyError('缓存 SHA256 不匹配: ' + target.name)
        return target
    errors = []
    for url in (pin['url'], pin.get('fallback_url')):
        if not url: continue
        if not url.lower().startswith('https://'):
            errors.append('拒绝非 HTTPS 官方依赖 URL')
            continue
        part = target.with_suffix('.part-' + uuid.uuid4().hex)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'BB-CN-Installer/0.1', 'Accept': 'application/octet-stream'})
            with urllib.request.urlopen(req, timeout=90) as response, open(part, 'xb') as out:
                total = 0
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > 1024**3: raise SafetyError('下载超出 1 GiB 限制')
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            if sha256(part) != pin['sha256']: raise SafetyError('下载 SHA256 不匹配')
            os.replace(part, target)
            return target
        except Exception as exc:
            errors.append(str(exc))
        finally:
            part.unlink(missing_ok=True)
    raise SafetyError('官方依赖下载失败；可将已校验官方 ZIP 放入缓存。' + '; '.join(errors))

def prepare(engine, launcher, cache, runner=subprocess.run):
    launch_option(engine.game)  # Reject unrepresentable commands before any cache writes.
    engine.validate()
    if sha256(launcher) != PINS['launcher']['sha256']: raise SafetyError('启动器 SHA256 不匹配')
    cache = Path(cache).absolute()
    no_links(cache)
    try: cache.relative_to(engine.game)
    except ValueError: pass
    else: raise SafetyError('缓存必须位于游戏目录之外')
    cache.mkdir(parents=True, exist_ok=True)
    translator = fetch_pinned(PINS['translator'], cache / 'bb-translator-windows.zip')
    texts = translation_archive(cache)
    # Keep the prefix through copying, native execution, logs and output too.
    work = filesystem_path(cache / ('prepared-' + uuid.uuid4().hex))
    work.mkdir()
    safe_extract(translator, work / 'translator')
    safe_extract(texts, work / 'texts')
    stage = work / 'game'
    (stage / 'data').mkdir(parents=True)
    (stage / 'win32').mkdir()
    dats = list((engine.game / 'data').glob('*.dat'))
    if not dats: raise SafetyError('没有原版 data/*.dat')
    for dat in dats:
        no_links(dat)
        durable_copy(dat, stage / 'data' / dat.name)
    roots = list((work / 'texts').glob('*/zh_CN.UTF-8/json'))
    if len(roots) != 1: raise SafetyError('官方文本目录不匹配')
    shutil.copytree(roots[0], stage / 'Battle-Brothers-CN/zh_CN.UTF-8/json')
    executable = work / 'translator/Windows/Release/bb_translator.exe'
    if not executable.is_file(): raise SafetyError('官方转换器目录不匹配')
    command = [str(executable), '--game-dir', str(stage)]
    result = runner(command, cwd=str(executable.parent), capture_output=True, timeout=3600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    def decode(data): return data.decode('utf-8', errors='replace') if isinstance(data, bytes) else (data or '')
    log = decode(result.stdout) + '\n--- STDERR ---\n' + decode(result.stderr)
    (work / 'translator.log').write_text(log, encoding='utf-8')
    if result.returncode != 0: raise SafetyError('转换器失败；请查看 ' + str(work / 'translator.log'))
    package = stage / 'data/zdata_cn.zip'
    audit_zip(package)
    output = work / 'zdata_cn.zip'
    durable_copy(package, output)
    warnings = ['第三方 MOD 兼容性未验证；仅从原版 .dat 生成，不合并 MOD。']
    if log.strip().replace('--- STDERR ---', '').strip():
        warnings.extend(line for line in log.splitlines() if line.strip() and line != '--- STDERR ---')
    if re.search(r'player|onDeath|modify failed', log, re.I):
        warnings.insert(0, '重要：检测到转换器补丁警告（已知 player / onDeath 修改失败）。不能保证该功能汉化完整。')
    prepared = Prepared(output, sha256(output), warnings)
    atomic_json(work / 'prepared.json', {'sha256': prepared.digest, 'warnings': warnings})
    return prepared

class WindowsPlatform:
    def version(self, path):
        import ctypes
        from ctypes import wintypes as w
        dll = ctypes.WinDLL('version', use_last_error=True)
        dll.GetFileVersionInfoSizeW.argtypes = (w.LPCWSTR, ctypes.POINTER(w.DWORD))
        dll.GetFileVersionInfoSizeW.restype = w.DWORD
        handle = w.DWORD()
        size = dll.GetFileVersionInfoSizeW(str(path), ctypes.byref(handle))
        if not size: raise SafetyError('无法读取 Windows FileVersion: ' + str(path))
        buf = ctypes.create_string_buffer(size)
        dll.GetFileVersionInfoW.argtypes = (w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p)
        if not dll.GetFileVersionInfoW(str(path), 0, size, buf): raise SafetyError('FileVersion 读取失败')
        pointer = ctypes.c_void_p()
        length = w.UINT()
        dll.VerQueryValueW.argtypes = (ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.UINT))
        if not dll.VerQueryValueW(buf, '\\', ctypes.byref(pointer), ctypes.byref(length)): raise SafetyError('缺少版本资源')
        fields = ctypes.cast(pointer, ctypes.POINTER(w.DWORD))
        if length.value < 24 or fields[0] != 0xFEEF04BD: raise SafetyError('版本资源无效')
        ms, ls = fields[2], fields[3]
        return f'{ms >> 16}.{ms & 65535}.{ls >> 16}.{ls & 65535}'

    def running(self):
        import csv
        result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode: raise SafetyError('无法检查游戏进程；拒绝修改')
        text = result.stdout.decode('mbcs', errors='replace')
        rows = list(csv.reader(text.splitlines()))
        if not rows or any(len(row) < 2 for row in rows): raise SafetyError('进程列表无效；拒绝修改')
        blocked = {'battlebrothers.exe', 'bb-launcher-steam.exe', 'bb-launcher-gog.exe'}
        return any(row[0].casefold() in blocked for row in rows)

    def steam(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
                return Path(winreg.QueryValueEx(key, 'SteamPath')[0])
        except OSError as e:
            raise SafetyError('未找到当前用户 Steam 注册表路径，请手动选择游戏目录') from e

@contextmanager
def game_lock(game):
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, w.BOOL, w.LPCWSTR)
    kernel.CreateMutexW.restype = w.HANDLE
    kernel.WaitForSingleObject.argtypes = (w.HANDLE, w.DWORD)
    kernel.ReleaseMutex.argtypes = (w.HANDLE,)
    kernel.CloseHandle.argtypes = (w.HANDLE,)
    name = 'Local\\BB-CN-' + hashlib.sha256(str(Path(game).absolute()).casefold().encode()).hexdigest()
    handle = kernel.CreateMutexW(None, False, name)
    if not handle: raise SafetyError('无法建立安装锁')
    result = kernel.WaitForSingleObject(handle, 0)
    if result not in (0, 0x80):
        kernel.CloseHandle(handle)
        raise SafetyError('另一安装操作正在进行')
    # Windows mutexes are reentrant: also reject nested operations on one thread.
    if name in _ACTIVE_LOCKS:
        kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)
        raise SafetyError('另一安装操作正在进行')
    _ACTIVE_LOCKS.add(name)
    try: yield
    finally:
        _ACTIVE_LOCKS.discard(name)
        kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)

_ACTIVE_LOCKS = set()

def locked(method=None, *, require_launch_option=False):
    if method is None:
        return lambda method: locked(method, require_launch_option=require_launch_option)
    @wraps(method)
    def call(self, *args, **kwargs):
        if require_launch_option:
            launch_option(self.game)  # Preflight before acquiring the operation lock.
        with game_lock(self.game): return method(self, *args, **kwargs)
    return call

class Engine:
    def __init__(self, game, platform=None):
        self.game = Path(game).absolute()
        self.platform = platform or WindowsPlatform()
        self.store = self.game / '.bb-cn-installer'

    def _paths(self):
        for rel in (*TARGETS, 'win32/BattleBrothers.exe', 'win32/steam_api.dll', '.bb-cn-installer'):
            no_links(self.game / rel)
        if self.store.exists():
            allowed = {'state.json', 'journal.json', 'state.tmp', 'journal.tmp',
                       'launch-options.json', 'launch-options.tmp',
                       *(f'{kind}-{i}' for kind in ('backup', 'undo', 'new', 'restore') for i in range(2))}
            for item in self.store.iterdir():
                if item.name not in allowed:
                    raise SafetyError('状态目录中存在意外文件：' + item.name)
                no_links(item)
                if item.is_dir():
                    raise SafetyError('状态目录中存在意外子目录')
            from steam_options import load
            load(self)  # Strict independent launch-option ownership schema, also for read-only checks.

    def validate(self, require_supported=True):
        self._paths()
        if not (self.game / 'win32/BattleBrothers.exe').is_file() or not (self.game / 'win32/steam_api.dll').is_file():
            raise SafetyError('缺少原版 BattleBrothers.exe / steam_api.dll')
        if require_supported and self.platform.version(self.game / 'win32/BattleBrothers.exe') != PINS['game_version']:
            raise SafetyError('仅支持游戏版本 ' + PINS['game_version'])
        if self.platform.running():
            raise SafetyError('请先退出游戏；本程序不会结束游戏进程')
        manifest = self.game.parent.parent / 'appmanifest_365360.acf'
        if not manifest.is_file():
            raise SafetyError('缺少 Steam appmanifest_365360.acf')
        text = manifest.read_text(encoding='utf-8-sig')
        app = parse_vdf(text).get('appstate', {})
        if app.get('appid') != '365360' or app.get('installdir') != self.game.name:
            raise SafetyError('Steam 清单与所选游戏不匹配')

    @locked(require_launch_option=True)
    def install(self, prepared, launcher, replace=False, accept_warnings=False):
        self.validate()
        for source in (prepared.path, launcher):
            no_links(source)
            if Path(source).absolute().is_relative_to(self.game):
                raise SafetyError('安装源必须位于游戏目录之外')
        if (self.store / 'journal.json').exists():
            raise SafetyError('存在未完成事务，请先恢复')
        if sha256(launcher) != PINS['launcher']['sha256']:
            raise SafetyError('启动器 SHA256 不匹配；请导入作者的 Steam v1.2.2')
        if sha256(prepared.path) != prepared.digest:
            raise SafetyError('准备文件在生成后发生变化')
        audit_zip(prepared.path)
        if prepared.warnings and not accept_warnings:
            raise SafetyError('必须明确接受转换警告后才能安装：' + '\n'.join(prepared.warnings))
        existing = self._state() if (self.store / 'state.json').exists() else None
        if existing and not self.check()['healthy']:
            raise SafetyError('已安装文件被修改；拒绝覆盖')
        if not existing and not replace:
            for rel in TARGETS:
                target = self.game / rel
                if target.exists():
                    raise SafetyError('目标文件已存在；需要显式备份替换许可')
        self.store.mkdir(exist_ok=True)
        entries = []
        for i, (rel, source) in enumerate(zip(TARGETS, (prepared.path, launcher))):
            target = self.game / rel
            backup = None
            if existing:
                backup = existing['entries'][i]['backup']
            elif target.exists():
                backup = f'backup-{i}'
                durable_copy(target, self.store / backup)
            entries.append({'path': rel, 'sha256': sha256(source), 'backup': backup, 'backup_sha256': sha256(self.store / backup) if backup else None})
        self._transaction((prepared.path, launcher), {'entries': entries})

    def _transaction(self, sources, newstate):
        if (self.store / 'journal.json').exists():
            raise SafetyError('存在未完成事务，请先恢复')
        oldstate = self._state() if (self.store / 'state.json').exists() else None
        rows = []
        for i, (rel, source) in enumerate(zip(TARGETS, sources)):
            target = self.game / rel
            before = f'undo-{i}' if target.exists() else None
            if before: durable_copy(target, self.store / before)
            if source is not None:
                durable_copy(source, self.store / f'new-{i}')
                expected = newstate['entries'][i]['sha256'] if newstate else oldstate['entries'][i]['backup_sha256']
                if sha256(self.store / f'new-{i}') != expected:
                    raise SafetyError('暂存文件校验失败；未写入游戏')
            rows.append({'path': rel, 'before': before, 'before_sha256': sha256(target) if before else None,
                         'after_sha256': sha256(source) if source is not None else None})
        atomic_json(self.store / 'journal.json', {'rows': rows, 'oldstate': oldstate})
        try:
            for i, (rel, source) in enumerate(zip(TARGETS, sources)):
                if source is None:
                    (self.game / rel).unlink(missing_ok=True)
                else:
                    os.replace(self.store / f'new-{i}', self.game / rel)
            if newstate is None:
                (self.store / 'state.json').unlink(missing_ok=True)
            else:
                atomic_json(self.store / 'state.json', newstate)
            (self.store / 'journal.json').unlink()
        except Exception:
            self._recover()
            raise

    @locked
    def recover(self):
        self._recover()

    def _recover(self):
        self.validate(require_supported=False)
        try:
            journal = json.loads((self.store / 'journal.json').read_text(encoding='utf-8'))
            if not isinstance(journal, dict) or set(journal) != {'rows', 'oldstate'}:
                raise ValueError('journal schema')
            if not isinstance(journal['rows'], list) or len(journal['rows']) != 2:
                raise ValueError('row count')
            if journal['oldstate'] is not None: self._validate_state(journal['oldstate'])
            for i, row in enumerate(journal['rows']):
                if not isinstance(row, dict) or set(row) != {'path', 'before', 'before_sha256', 'after_sha256'}:
                    raise ValueError('row schema')
                for field in ('before_sha256', 'after_sha256'):
                    value = row[field]
                    if value is not None and (not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value)):
                        raise ValueError('journal digest')
                if row['path'] != TARGETS[i] or row['before'] not in (None, f'undo-{i}'):
                    raise ValueError('journal path')
                if (row['before'] is None) != (row['before_sha256'] is None):
                    raise ValueError('before/hash mismatch')
                if journal['oldstate'] is not None and row['before_sha256'] != journal['oldstate']['entries'][i]['sha256']:
                    raise ValueError('journal does not match old owned state')
                if row['before'] and sha256(self.store / row['before']) != row['before_sha256']:
                    raise ValueError('undo changed')
                target = self.game / row['path']
                current = sha256(target) if target.exists() else None
                if current not in (row['before_sha256'], row['after_sha256']):
                    raise ValueError('target changed since interrupted transaction')
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise SafetyError('恢复日志不可信，未执行恢复') from exc
        for i, row in enumerate(journal['rows']):
            target = self.game / row['path']
            if row['before']:
                durable_copy(self.store / row['before'], self.store / f'restore-{i}')
                os.replace(self.store / f'restore-{i}', target)
            else:
                target.unlink(missing_ok=True)
        if journal['oldstate'] is None:
            (self.store / 'state.json').unlink(missing_ok=True)
        else:
            atomic_json(self.store / 'state.json', journal['oldstate'])
        (self.store / 'journal.json').unlink()

    def _validate_state(self, state):
        try:
            if (not isinstance(state, dict) or set(state) != {'entries'} or
                not isinstance(state['entries'], list) or len(state['entries']) != 2):
                raise ValueError('entry count/schema')
            for i, e in enumerate(state['entries']):
                if not isinstance(e, dict) or set(e) != {'path', 'sha256', 'backup', 'backup_sha256'}:
                    raise ValueError('entry schema')
                if e['path'] != TARGETS[i] or not isinstance(e['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', e['sha256']):
                    raise ValueError('target/hash')
                if e['backup'] not in (None, f'backup-{i}'):
                    raise ValueError('backup path')
                if (e['backup'] is None) != (e['backup_sha256'] is None):
                    raise ValueError('backup/hash mismatch')
                if e['backup'] and (not isinstance(e['backup_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', e['backup_sha256']) or sha256(self.store / e['backup']) != e['backup_sha256']):
                    raise ValueError('backup changed')
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise SafetyError('状态或备份不可信；拒绝写入') from exc
        return state

    def _state(self):
        try:
            return self._validate_state(json.loads((self.store / 'state.json').read_text(encoding='utf-8')))
        except (ValueError, OSError) as exc:
            raise SafetyError('无法读取安装状态') from exc

    def check(self):
        self._paths()
        if (self.store / 'journal.json').exists():
            raise SafetyError('存在未完成事务，请先恢复')
        state = self._state()
        return {'healthy': all((self.game / e['path']).is_file() and sha256(self.game / e['path']) == e['sha256'] for e in state['entries'])}

    def diagnose(self):
        result = {'game': str(self.game), 'supported': False, 'installed': (self.store / 'state.json').is_file(), 'healthy': False}
        try:
            self.validate()
            result['supported'] = True
            result['version'] = self.platform.version(self.game / 'win32/BattleBrothers.exe')
            if result['installed']: result.update(self.check())
            result['launch_option'] = launch_option(self.game)
        except (SafetyError, OSError) as exc:
            result['error'] = str(exc)
        result['pending_recovery'] = (self.store / 'journal.json').exists()
        return result

    @locked
    def set_steam_options(self, backend=None):
        self.validate()
        if not self.check()['healthy']:
            raise SafetyError('请先安装并通过文件校验')
        from steam_options import set_options
        return set_options(self, backend)

    @locked
    def restore_steam_options(self, backend=None):
        self.validate(require_supported=False)
        if (self.store / 'journal.json').exists():
            raise SafetyError('存在未完成事务，请先恢复')
        from steam_options import restore_options
        return restore_options(self, backend)

    @locked
    def uninstall(self, steam_backend=None):
        self.validate(require_supported=False)
        if not self.check()['healthy']:
            raise SafetyError('已安装文件被修改；拒绝卸载')
        state = self._state()
        from steam_options import restore_options
        result = restore_options(self, steam_backend)
        self._transaction(tuple(self.store / e['backup'] if e['backup'] else None for e in state['entries']), None)
        return result
