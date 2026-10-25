# -*- coding: utf-8 -*-
"""Build the self-contained WebView2 Netflix player exe (Windows).

The result `dist/nfbridge_player/` (nfbridge_player.exe + _internal/) bundles Python +
pywebview + fonttools, so the Kodi addon needs no external Python. A onedir build, not
onefile: a onefile exe unpacks itself to %TEMP% on every launch (~1 s before the daemon
even starts). It is dual-purpose:
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
    ico = os.path.join(HERE, 'icon.ico')
    cmd = [sys.executable, '-m', 'PyInstaller', '--onedir', '--noconsole',
           '--name', 'nfbridge_player', '--paths', HERE,
           '--hidden-import', 'fontfix', '--noconfirm',
           # the exe's own icon (Explorer / taskbar) + a copy inside the bundle so the
           # running window can stamp it on itself (webview_host._set_window_icon)
           '--icon', ico,
           '--add-data', ico + os.pathsep + '.',
           '--distpath', os.path.join(HERE, 'dist'),
           '--workpath', os.path.join(HERE, 'build'),
           '--specpath', os.path.join(HERE, 'build'),
           os.path.join(HERE, 'webview_host.py')]
    subprocess.run(cmd, check=True)
    out = os.path.join(HERE, 'dist', 'nfbridge_player')
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(out) for f in fs)
    print('built:', out, '(%.1f MB)' % (size / 1048576.0))


if __name__ == '__main__':
    main()
