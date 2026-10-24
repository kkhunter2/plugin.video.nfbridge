# -*- coding: utf-8 -*-
"""Build a Kodi-installable zip of the add-on.

Builds the WebView2 runtime exe (so embedded mode works out of the box) and zips the
add-on under a top-level `plugin.video.nfbridge/` folder, excluding dev/build cruft.

    python tools/package.py            # (re)build exe if missing OR older than its sources, then zip
    python tools/package.py --no-build # zip whatever is present
"""
import os
import re
import sys
import zipfile
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_ID = 'plugin.video.nfbridge'
EXCLUDE_DIRS = {'.git', '__pycache__', 'build', 'cache', 'httpcache', 'profile',
                'node_modules', 'tools'}
EXCLUDE_EXT = {'.pyc', '.spec', '.pyo', '.log'}
EXCLUDE_FILES = {'.gitignore'}
# the exe bundles these; if any is newer than the exe, the exe is stale -> rebuild
EXE_SOURCES = ('webview_host.py', 'fontfix.py', 'build_exe.py', 'icon.ico')


def _version():
    with open(os.path.join(ROOT, 'addon.xml'), encoding='utf-8') as fh:
        m = re.search(r'<addon[^>]+\bversion="([^"]+)"', fh.read(), re.S)
    return m.group(1) if m else '0.0.0'


def _exe_stale(exe):
    if not os.path.exists(exe):
        return True
    wv = os.path.join(ROOT, 'runtime', 'webview')
    exe_mtime = os.path.getmtime(exe)
    for name in EXE_SOURCES:
        p = os.path.join(wv, name)
        if os.path.exists(p) and os.path.getmtime(p) > exe_mtime:
            print('runtime exe is older than %s -> rebuild' % name)
            return True
    return False


def main(argv):
    exe = os.path.join(ROOT, 'runtime', 'webview', 'dist', 'nfbridge_player.exe')
    if '--no-build' not in argv and _exe_stale(exe):
        print('building runtime exe...')
        subprocess.run([sys.executable, os.path.join(ROOT, 'runtime', 'webview', 'build_exe.py')],
                       check=True)

    out = os.path.join(os.path.dirname(ROOT), '%s-%s.zip' % (ADDON_ID, _version()))
    n = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for f in filenames:
                if os.path.splitext(f)[1].lower() in EXCLUDE_EXT or f in EXCLUDE_FILES:
                    continue
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, ROOT)
                z.write(full, os.path.join(ADDON_ID, rel))
                n += 1
    print('wrote %s (%d files, %.1f MB)' % (out, n, os.path.getsize(out) / 1048576.0))


if __name__ == '__main__':
    main(sys.argv[1:])
