"""Conservative, read-only discovery for the installer GUI."""
from pathlib import Path

from installer_core import (Engine, PINS, SafetyError, WindowsPlatform,
                            discover_games, no_links, parse_vdf, sha256)


_EXCLUDED = frozenset({
    '.git', '.hg', '.svn', '.cache', 'cache', '__pycache__', 'node_modules',
    '.venv', 'venv', 'build', 'dist', 'target', 'bb-cn-build-deps',
    'battle-brothers-cn-installer',
})


def _order(path):
    return str(path).casefold(), str(path)


def _download_candidates(root, depth=0):
    """Never descend through links, repositories, or below three directories."""
    root = Path(root).absolute()
    try:
        no_links(root)
        if not root.is_dir():
            return
        # Check the directory itself before listing it; do not enter repositories.
        if depth and (root.name.casefold() in _EXCLUDED or
                      any((root / marker).exists() for marker in ('.git', '.hg', '.svn'))):
            return
        yield root / 'bb-launcher-steam.exe'
        if depth == 3:
            return
        for child in sorted(root.iterdir(), key=_order):
            if child.name.casefold() not in _EXCLUDED:
                yield from _download_candidates(child, depth + 1)
    except (OSError, SafetyError, ValueError):
        return


def _check_manifest(path):
    no_links(path)
    if path.is_file():
        app = parse_vdf(path.read_text(encoding='utf-8-sig')).get('appstate', {})
        if not isinstance(app, dict) or not all(isinstance(app.get(key, ''), str)
                                                for key in ('appid', 'installdir')):
            raise SafetyError('Steam appmanifest 格式无效')
        name = app.get('installdir', '')
        if (app.get('appid') == '365360' and name and
                name not in ('.', '..') and not any(c in name for c in ('/', '\\', ':'))):
            no_links(path.parent / 'common' / name)


def _safe_discover_games(steam):
    """Preflight metadata before core discovery reads or resolves library paths."""
    steam = Path(steam).absolute()
    no_links(steam)
    folder = steam / 'steamapps/libraryfolders.vdf'
    no_links(folder)
    libraries = [steam]
    if folder.is_file():
        entries = parse_vdf(folder.read_text(encoding='utf-8-sig')).get('libraryfolders', {})
        if not isinstance(entries, dict):
            raise SafetyError('Steam libraryfolders 格式无效')
        for key, entry in entries.items():
            if key.isdigit():
                value = entry.get('path') if isinstance(entry, dict) else entry
                if value:
                    library = Path(value).absolute()
                    no_links(library)  # Must precede discover_games' resolve().
                    libraries.append(library)
    for library in libraries:
        _check_manifest(library / 'steamapps/appmanifest_365360.acf')
    return discover_games(steam)


def _known_downloads():
    """Read Windows' redirected Downloads known folder without creating it."""
    try:
        import ctypes
        import uuid
        from ctypes import wintypes as w

        shell = ctypes.WinDLL('shell32', use_last_error=True)
        ole = ctypes.WinDLL('ole32', use_last_error=True)
        # FOLDERID_Downloads; GUID layout is little endian on Windows.
        folder_id = (ctypes.c_ubyte * 16).from_buffer_copy(
            uuid.UUID('374de290-123f-4565-9164-39c4925e467b').bytes_le)
        pointer = ctypes.c_void_p()
        shell.SHGetKnownFolderPath.argtypes = (ctypes.c_void_p, w.DWORD,
                                               w.HANDLE, ctypes.POINTER(ctypes.c_void_p))
        shell.SHGetKnownFolderPath.restype = ctypes.c_long
        ole.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
        ole.CoTaskMemFree.restype = None
        try:
            # KF_FLAG_DONT_VERIFY, deliberately never KF_FLAG_CREATE.
            status = shell.SHGetKnownFolderPath(ctypes.byref(folder_id), 0x4000,
                                                None, ctypes.byref(pointer))
            if status == 0 and pointer.value:
                path = Path(ctypes.wstring_at(pointer))
                if path.is_absolute():
                    return path
        finally:
            if pointer.value:
                ole.CoTaskMemFree(pointer)
    except (AttributeError, ImportError, OSError, ValueError):
        pass
    return Path.home() / 'Downloads'


def detect_paths(game=None, *, platform=None, downloads_roots=None):
    """Return only validated candidates; absence never authorizes a write."""
    platform = platform if platform is not None else WindowsPlatform()
    notes = []
    games = []
    try:
        candidates = _safe_discover_games(platform.steam())
        for candidate in candidates:
            try:
                no_links(Path(candidate).absolute().parent.parent / 'appmanifest_365360.acf')
                engine = Engine(candidate, platform=platform)
                engine.validate()
                if engine.game not in games:
                    games.append(engine.game)
            except (OSError, SafetyError, ValueError) as exc:
                notes.append(str(exc))
    except (OSError, SafetyError, ValueError) as exc:
        notes.append(str(exc))
    explicit = None
    if game is not None:
        try:
            _check_manifest(Path(game).absolute().parent.parent / 'appmanifest_365360.acf')
            engine = Engine(game, platform=platform)
            engine.validate()
            explicit = engine.game
            if explicit not in games:
                games.append(explicit)
        except (OSError, SafetyError, ValueError) as exc:
            notes.append('所选游戏未通过校验：' + str(exc))
    games.sort(key=lambda path: (str(path).casefold(), str(path)))
    selected = explicit if game is not None else (games[0] if len(games) == 1 else None)
    if not games:
        notes.append('未找到通过校验的受支持 Steam 游戏，请手动选择。')
    if len(games) > 1 and selected is None:
        notes.append('找到多个受支持游戏，请手动选择。')
    roots = downloads_roots if downloads_roots is not None else [_known_downloads()]
    candidates = [path / 'win32/bb-launcher-steam.exe' for path in games]
    candidates.extend(sorted({candidate for root in roots
                              for candidate in _download_candidates(root)}, key=_order))
    launchers = []
    for candidate in candidates:
        try:
            no_links(candidate)
            if candidate.is_file() and sha256(candidate) == PINS['launcher']['sha256']:
                if candidate not in launchers:
                    launchers.append(candidate)
        except (OSError, SafetyError, ValueError) as exc:
            notes.append(str(exc))
    # Installed copies are component evidence, not acceptable installation sources.
    sources = [path for path in launchers
               if not any(path.absolute().is_relative_to(game) for game in games)]
    if not sources:
        if launchers:
            notes.append('检测到游戏目录内已安装的已校验启动器；仅作组件状态参考，不能用作安装源。')
        notes.append('未找到游戏目录之外的已校验启动器，请从作者页面下载并手动选择。')
    return {'games': games, 'game': selected, 'launchers': launchers,
            'launcher': sources[0] if sources else None, 'note': '\n'.join(notes)}
