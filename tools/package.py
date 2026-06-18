# -*- coding: utf-8 -*-
"""Build a Kodi-installable zip of the add-on.

Builds the WebView2 runtime exe (so embedded mode works out of the box) and zips the
add-on under a top-level `plugin.video.nfbridge/` folder, excluding dev/build cruft.

    python tools/package.py            # build exe if missing, then zip
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
                'node_modules', 'electron', 'tools'}
EXCLUDE_EXT = {'.pyc', '.spec', '.pyo'}


def _version():
    with open(os.path.join(ROOT, 'addon.xml'), encoding='utf-8') as fh:
        m = re.search(r'<addon[^>]+\bversion="([^"]+)"', fh.read(), re.S)
    return m.group(1) if m else '0.0.0'


def main(argv):
    exe = os.path.join(ROOT, 'runtime', 'webview', 'dist', 'nfbridge_player.exe')
    if '--no-build' not in argv and not os.path.exists(exe):
        print('building runtime exe...')
        subprocess.run([sys.executable, os.path.join(ROOT, 'runtime', 'webview', 'build_exe.py')],
                       check=False)

    out = os.path.join(os.path.dirname(ROOT), '%s-%s.zip' % (ADDON_ID, _version()))
    n = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for f in filenames:
                if os.path.splitext(f)[1].lower() in EXCLUDE_EXT:
                    continue
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, ROOT)
                z.write(full, os.path.join(ADDON_ID, rel))
                n += 1
    print('wrote %s (%d files, %.1f MB)' % (out, n, os.path.getsize(out) / 1048576.0))


if __name__ == '__main__':
    main(sys.argv[1:])
