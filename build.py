"""Build both onedir apps with a preconfigured venv; never install globally."""
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def _copy_if_file(source, target):
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return True
    return False


def _extract_zipfs_license(dll, member, target):
    if not dll.is_file() or not zipfile.is_zipfile(dll):
        return False
    with zipfile.ZipFile(dll) as archive:
        try:
            info = archive.getinfo(member)
        except KeyError:
            return False
        # The member name is fixed by the Tcl/Tk runtime layout.  Refuse
        # anything that could escape the destination even if a runtime is
        # replaced by an unexpected archive.
        candidate = Path(info.filename)
        if candidate.is_absolute() or '..' in candidate.parts:
            raise RuntimeError(f'unsafe runtime license path: {info.filename}')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(info))
        return True


def copy_runtime_licenses(output, base):
    """Copy Python, Tcl and Tk terms into a frozen distribution.

    Python 3.14 can embed Tcl/Tk scripts and their license.terms files in
    zipfs inside the native DLLs, so looking only for base/tcl is incomplete.
    Fail closed if the Python runtime terms or either Tcl/Tk terms are absent.
    """
    output = Path(output)
    base = Path(base)
    output.mkdir(parents=True, exist_ok=True)
    python_license = next((p for p in (base / 'LICENSE.txt', base / 'LICENSE') if p.is_file()), None)
    if python_license is None:
        raise RuntimeError('Python runtime license not found')
    shutil.copyfile(python_license, output / 'Python-LICENSE.txt')

    tcl_ok = _extract_zipfs_license(base / 'DLLs' / 'tcl90.dll', 'tcl_library/license.terms', output / 'Tcl-license.terms')
    tk_ok = _extract_zipfs_license(base / 'DLLs' / 'tcl9tk90.dll', 'tk_library/license.terms', output / 'Tk-license.terms')
    for folder, name in ((base / 'tcl', 'Tcl'), (base / 'tk', 'Tk'), (base / 'Lib' / 'tkinter', 'Tkinter')):
        for source in folder.rglob('*') if folder.is_dir() else ():
            if source.is_file() and source.name.casefold() in {'license.terms', 'license.txt', 'copying', 'copyright'}:
                target = output / f'{name}-{source.name}'
                if name == 'Tcl' and not tcl_ok:
                    shutil.copyfile(source, target); tcl_ok = True
                elif name in {'Tk', 'Tkinter'} and not tk_ok:
                    shutil.copyfile(source, target); tk_ok = True
    if not tcl_ok or not tk_ok:
        raise RuntimeError('Tcl/Tk runtime license terms not found')
    return output


def commands(root):
    root = Path(root).absolute()
    common = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--noupx',
              '--distpath', str(root / 'dist'), '--workpath', str(root / 'build'),
              '--specpath', str(root / 'build'), '--add-data', str(root / 'dependencies.json') + ';.']
    return [common + [mode, '--name', name, str(root / 'bb_cn_installer.py')]
            for mode, name in (('--windowed', 'BB-CN-Installer'), ('--console', 'BB-CN-Installer-CLI'))]


def main():
    if sys.platform != 'win32': raise SystemExit('Windows build required')
    if importlib.metadata.version('pyinstaller') != '6.22.3': raise SystemExit('Use pinned requirements-build.txt in a separate venv')
    root = Path(__file__).resolve().parent
    scratch = Path(os.environ.get('BBCN_TEST_SCRATCH', str(Path.home() / 'AppData/Local/hermes/cache/scratch')))
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TMPDIR=str(scratch), TMP=str(scratch), TEMP=str(scratch), BBCN_TEST_SCRATCH=str(scratch))
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=root, env=env, check=True)
    for command in commands(root):
        subprocess.run(command, cwd=root, env=env, check=True)
        name = command[command.index('--name') + 1]
        output = root / 'dist' / name
        for filename in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'dependencies.json'):
            shutil.copyfile(root / filename, output / filename)
        licenses = output / 'runtime-licenses'
        copy_runtime_licenses(licenses, Path(sys.base_prefix))
        dist = importlib.metadata.distribution('pyinstaller')
        for entry in dist.files or []:
            if 'license' in entry.name.casefold() or 'copying' in entry.name.casefold():
                source = Path(dist.locate_file(entry))
                if source.is_file(): shutil.copyfile(source, licenses / ('PyInstaller-' + source.name))
        print('BUILT', output / (name + '.exe'))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
