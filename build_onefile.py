"""Build one windowed executable; legacy onedir artifacts are not published.

This packaging entry point does not imply translation resources or automatic
Steam navigation are complete. Those acceptance gates remain independent.
"""
import importlib.metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys

from build import copy_runtime_licenses
from installer_core import PINS, SafetyError, sha256, fetch_pinned, audit_zip
import zipfile


def stage_texts(destination, source):
    """Embed the exact GPL source archive, not transformed game scripts."""
    destination, source = Path(destination), Path(source)
    if sha256(source) != PINS['texts']['sha256']:
        raise SafetyError('Translation source SHA256 mismatch')
    audit_zip(source)
    with zipfile.ZipFile(source) as archive:
        licenses = [n for n in archive.namelist() if n.endswith('/LICENSE')]
        if len(licenses) != 1:
            raise SafetyError('No unique translation LICENSE')
        license_text = archive.read(licenses[0]).decode('utf-8')
        if 'GNU GENERAL PUBLIC LICENSE' not in license_text or 'Version 3' not in license_text:
            raise SafetyError('Expected GPLv3 translation license')
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination / 'translation-source.zip')
    (destination / 'TRANSLATION-LICENSE.txt').write_text(license_text, encoding='utf-8')
    (destination / 'TRANSLATION-NOTICE.txt').write_text(
        'Battle-Brothers-CN translation source by shabbywu and contributors.\n'
        'License: GNU GPL v3; full license and exact unmodified source archive included.\n'
        'Source: https://github.com/shabbywu/Battle-Brothers-CN\n'
        'Revision: ' + PINS['texts']['commit'] + '\n'
        'Changes: none to the source archive; embedded as a separate data resource.\n'
        'Generated game scripts, translator and launcher are not included.\n', encoding='utf-8')
    return destination


def command(root, licenses):
    root = Path(root).absolute()
    licenses = Path(licenses).absolute()
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
            '--onefile', '--windowed', '--noupx',
            '--distpath', str(root / 'dist-onefile'),
            '--workpath', str(root / 'build' / 'onefile'),
            '--specpath', str(root / 'build' / 'onefile')]
    for filename in ('dependencies.json', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
        args += ['--add-data', str(root / filename) + ';.']
    args += ['--add-data', str(licenses) + ';runtime-licenses']
    args += ['--add-data', str(root / 'build' / 'onefile-resources') + ';resources']
    return args + ['--name', 'BB-CN-Installer', str(root / 'bb_cn_installer.py')]


def main():
    if sys.platform != 'win32':
        raise SystemExit('Windows build required')
    if importlib.metadata.version('pyinstaller') != '6.22.3':
        raise SystemExit('Use pinned requirements-build.txt in a separate venv')
    root = Path(__file__).resolve().parent
    scratch = Path(os.environ.get('BBCN_TEST_SCRATCH', str(Path.home() / 'AppData/Local/hermes/cache/scratch')))
    scratch.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TMPDIR=str(scratch), TMP=str(scratch), TEMP=str(scratch), BBCN_TEST_SCRATCH=str(scratch))
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=root, env=env, check=True)
    licenses = copy_runtime_licenses(root / 'build' / 'onefile-licenses', Path(sys.base_prefix))
    dist = importlib.metadata.distribution('pyinstaller')
    for entry in dist.files or []:
        if 'license' in entry.name.casefold() or 'copying' in entry.name.casefold():
            source = Path(str(dist.locate_file(entry)))
            if source.is_file():
                shutil.copyfile(source, licenses / ('PyInstaller-' + source.name))
    source = fetch_pinned(PINS['texts'], Path(os.environ.get('BBCN_TEXT_SOURCE', str(root.parent / 'BB-CN-build-deps' / 'translation-source.zip'))))
    stage_texts(root / 'build' / 'onefile-resources', source)
    subprocess.run(command(root, licenses), cwd=root, env=env, check=True)
    print('BUILT', root / 'dist-onefile' / 'BB-CN-Installer.exe')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
