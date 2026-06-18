# -*- coding: utf-8 -*-
"""Make any subtitle TTF/OTF usable by Chromium/WebView2.

Chromium's OpenType Sanitizer (OTS) rejects fonts that FreeType/Kodi load fine
(e.g. a malformed `post` table -> "Invalid font data in ArrayBuffer"). A fontTools
*subset* rebuild (drop hinting + layout tables, recompile glyf/post) yields an
OTS-valid WOFF2. Result is cached next to the source by mtime.
"""
import os
import hashlib


def to_web_woff2(src_path, cache_dir):
    """Return path to an OTS-valid WOFF2 built from src_path (cached)."""
    from fontTools.ttLib import TTFont
    from fontTools.subset import Subsetter, Options

    st = os.stat(src_path)
    key = hashlib.md5(('%s|%d|%d' % (src_path, st.st_size, int(st.st_mtime))).encode()).hexdigest()[:16]
    out = os.path.join(cache_dir, 'sub_' + key + '.woff2')
    if os.path.exists(out):
        return out
    if not os.path.isdir(cache_dir):
        os.makedirs(cache_dir)

    opt = Options()
    opt.hinting = False
    opt.desubroutinize = True
    opt.notdef_outline = True
    opt.glyph_names = False
    opt.recalc_timestamp = False
    opt.drop_tables = ['DSIG', 'LTSH', 'hdmx', 'VDMX', 'gasp', 'fpgm', 'prep', 'cvt ',
                       'GPOS', 'GSUB', 'GDEF', 'kern', 'FFTM', 'meta']
    font = TTFont(src_path, fontNumber=0)
    ss = Subsetter(options=opt)
    # cover Basic Latin + full Hangul + CJK symbols/punct + halfwidth/fullwidth
    unicodes = (list(range(0x20, 0x7F)) + list(range(0xA0, 0x100)) +
                list(range(0x3000, 0x303F)) + list(range(0x3130, 0x3190)) +
                list(range(0xAC00, 0xD7A4)) + list(range(0xFF00, 0xFFEF)) +
                list(range(0x2000, 0x2070)))
    ss.populate(unicodes=unicodes)
    ss.subset(font)
    font.flavor = 'woff2'
    font.save(out)
    return out


def woff2_base64(woff2_path):
    import base64
    return base64.b64encode(open(woff2_path, 'rb').read()).decode()


def main(argv):
    """CLI: `fontfix.py <src_font> <cache_dir> [result_file]`.

    Writes the OTS-valid WOFF2 path (or 'ERROR: <msg>') to result_file when given —
    robust for a windowed/--noconsole exe where stdout is unreliable — else to
    stdout/stderr. Exit 0 on success, 2 on a font that cannot be parsed/rebuilt.
    """
    import sys
    if len(argv) < 3:
        sys.stderr.write('usage: fontfix <src_font> <cache_dir> [result_file]\n')
        return 2
    src, cache = argv[1], argv[2]
    result_file = argv[3] if len(argv) > 3 else None

    def emit(text):
        if result_file:
            try:
                with open(result_file, 'w', encoding='utf-8') as fh:
                    fh.write(text)
            except Exception:
                pass
        else:
            sys.stdout.write(text)

    if not os.path.isfile(src):
        emit('ERROR: font not found: %s' % src)
        return 2
    try:
        emit(to_web_woff2(src, cache))
        return 0
    except Exception as exc:  # noqa
        emit('ERROR: %s' % exc)
        sys.stderr.write('ERROR: %s\n' % exc)
        return 2


if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv))
