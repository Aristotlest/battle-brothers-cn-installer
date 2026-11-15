# Third-party notices

The MIT license in this repository covers only the installer authors' original code. It does not grant any rights to Battle Brothers, Steam, third-party translation texts, launchers, translators, fonts, or generated game scripts. The new single-EXE build embeds only the pinned, unmodified GPLv3 translation source archive as separate data, with its complete source and license. No game files, launcher executable, translator executable, or generated localization ZIP are bundled by that build. Legacy v0.1.1 packages contain no translation archive.

## Translation texts

- Authors: shabbywu and Battle-Brothers-CN contributors.
- Source: https://github.com/shabbywu/Battle-Brothers-CN
- Pinned revision: `b72af34749a31aee59139ba4e2455f65f924a5a7`.
- Upstream license: GNU GPL v3 (the pinned archive includes `LICENSE`).
- Source/license: https://github.com/shabbywu/Battle-Brothers-CN/blob/b72af34749a31aee59139ba4e2455f65f924a5a7/LICENSE
- Single-EXE builds include the exact unmodified source ZIP, its complete GPLv3 LICENSE and TRANSLATION-NOTICE.txt under `resources/`. The archive itself supplies the corresponding preferred source; it is not relicensed as MIT. Source checkouts without a bundled archive fetch the same pinned upstream resource. No generated game scripts are embedded.

## bb-translator

- Author: shabbywu and upstream contributors.
- Version: v2.0.0.
- Source and corresponding release: https://github.com/shabbywu/bb-translator/tree/v2.0.0 and https://github.com/shabbywu/bb-translator/releases/tag/v2.0.0
- Upstream license: GNU GPL v3, https://github.com/shabbywu/bb-translator/blob/main/LICENSE
- Invoked as a separate executable from the user's cache. Includes upstream Python runtime and font assets; upstream package terms remain applicable. Font Awesome, DroidSans and other font names in that package are not assets authored by this project. Consult their upstream licenses before any redistribution. This installer does not redistribute that package.

## awesome-battle-brothers Steam launcher

- Author/source: shabbywu, https://github.com/shabbywu/awesome-battle-brothers
- Source license: GNU GPL v3, https://github.com/shabbywu/awesome-battle-brothers/blob/main/LICENSE
- Official binary distribution: https://www.nexusmods.com/battlebrothers/mods/719?tab=files
- Steam v1.2.2 is user-imported and SHA256-pinned; source license does not imply permission to relicense Nexus artifacts or their bundled dependencies. Follow the author's distribution terms. Never import the GOG variant into this Steam installer.

## Proprietary game and Steam

Battle Brothers and its scripts, data, artwork and marks belong to their respective owners, including Overhype Studios. Steam and its API belong to Valve and their respective licensors. A legally obtained local Steam copy is required. Generated `zdata_cn.zip` includes transformed game scripts: keep it local and do not publish it. This installer is not an achievement unlocker and has no affiliation with these owners.

## Frozen Python application

Source operation needs only Python's standard library including tkinter. Frozen builds include the Python runtime (PSF license and bundled third-party notices), Tcl/Tk (their licenses), and PyInstaller bootloader (GPL with its distribution exception). PyInstaller: https://pyinstaller.org/en/stable/license.html ; Python: https://docs.python.org/3/license.html ; Tcl/Tk: https://www.tcl.tk/software/tcltk/license.html . `build.py` copies the build runtime's available license files into the distribution and retains bundled Tcl/Tk runtime assets. Those components are not relicensed by this project's MIT license.
