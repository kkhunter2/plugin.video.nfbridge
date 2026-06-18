# -*- coding: utf-8 -*-
"""Build the self-contained WebView2 Netflix player exe (Windows).

The result `dist/nfbridge_player.exe` bundles Python + pywebview + fonttools, so the
Kodi addon needs no external Python. It is dual-purpose:
    nfbridge_player.exe                       -> the WebView2 Netflix player
    nfbridge_player.exe fontfix <src> <cache> [result_file]  -> OTS-repair a font

Prereqs (in the building Python):
    pip install pywebview fonttools brotli pyinstaller

Usage:
    python build_exe.py
"""
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    cmd = [sys.executable, '-m', 'PyInstaller', '--onefile', '--noconsole',
           '--name', 'nfbridge_player', '--paths', HERE,
           '--hidden-import', 'fontfix', '--noconfirm',
           '--distpath', os.path.join(HERE, 'dist'),
           '--workpath', os.path.join(HERE, 'build'),
           '--specpath', os.path.join(HERE, 'build'),
           os.path.join(HERE, 'webview_host.py')]
    subprocess.run(cmd, check=True)
    out = os.path.join(HERE, 'dist', 'nfbridge_player.exe')
    print('built:', out, '(%.1f MB)' % (os.path.getsize(out) / 1048576.0))


if __name__ == '__main__':
    main()
