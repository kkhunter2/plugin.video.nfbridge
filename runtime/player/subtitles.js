/*
 * NFSubs — self-contained subtitle re-render module for the embedded player.
 *
 * Netflix subtitles are NOT DRM-protected: they are TTML/IMSC1 (text) or WebVTT
 * files fetched in the clear. This module parses them into cues and renders a
 * styled overlay over the <video>, synced to video.currentTime — giving full
 * "Kodi-grade" font/size/color/outline/position control instead of Netflix's.
 *
 * Exposes window.NFSubs:
 *   parseTTML(xmlString) -> cues[]      ({start,end,text})  (seconds)
 *   parseVTT(text)       -> cues[]
 *   mount(videoEl, style?)              create the overlay
 *   setCues(cues)                       set/replace active cue list
 *   setStyle(style)                     update appearance live
 *   start() / stop()                    sync loop on/off
 */
(function () {
  'use strict';

  var DEFAULT_STYLE = {
    fontFamily: 'Arial, "Noto Sans", sans-serif',
    fontPct: 4.5,            // % of video height
    color: '#FFFFFF',
    outline: '#000000',
    outlinePx: 2,
    bgAlpha: 0.0,            // background box opacity (0 = none)
    bottomPct: 8,           // distance from bottom (% of video height)
    bold: false
  };

  function clamp(n, a, b) { return Math.max(a, Math.min(b, n)); }

  // ---- time parsing (clock "HH:MM:SS.mmm", frames "HH:MM:SS:FF", ticks "NNNNt") ----
  function parseTime(v, tickRate, fps) {
    if (v == null) return 0;
    v = String(v).trim();
    if (/t$/.test(v)) return parseFloat(v) / (tickRate || 10000000);
    if (/^[0-9.]+s$/.test(v)) return parseFloat(v);
    var p = v.split(':');
    if (p.length === 3) {
      return (+p[0]) * 3600 + (+p[1]) * 60 + parseFloat(p[2]);
    }
    if (p.length === 4) { // HH:MM:SS:FF
      return (+p[0]) * 3600 + (+p[1]) * 60 + (+p[2]) + (+p[3]) / (fps || 24);
    }
    return parseFloat(v) || 0;
  }

  function parseTTML(xml) {
    var doc = new DOMParser().parseFromString(xml, 'application/xml');
    var tt = doc.documentElement;
    var tickRate = parseInt(tt.getAttribute('ttp:tickRate') || tt.getAttribute('tickRate') || '10000000', 10);
    var fps = parseInt(tt.getAttribute('ttp:frameRate') || tt.getAttribute('frameRate') || '24', 10);
    var cues = [];
    var ps = doc.getElementsByTagName('p');
    for (var i = 0; i < ps.length; i++) {
      var p = ps[i];
      var start = parseTime(p.getAttribute('begin'), tickRate, fps);
      var end = parseTime(p.getAttribute('end'), tickRate, fps);
      cues.push({ start: start, end: end, text: nodeText(p) });
    }
    cues.sort(function (a, b) { return a.start - b.start; });
    return cues;
  }

  function nodeText(node) {
    var out = '';
    for (var i = 0; i < node.childNodes.length; i++) {
      var c = node.childNodes[i];
      if (c.nodeType === 3) out += c.nodeValue;
      else if (c.nodeName.toLowerCase() === 'br') out += '\n';
      else out += nodeText(c);
    }
    return out.replace(/\s+\n/g, '\n').trim();
  }

  function parseVTT(text) {
    var cues = [];
    var blocks = text.replace(/\r/g, '').split('\n\n');
    var re = /(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})/;
    for (var i = 0; i < blocks.length; i++) {
      var lines = blocks[i].split('\n');
      for (var j = 0; j < lines.length; j++) {
        var m = re.exec(lines[j]);
        if (m) {
          cues.push({
            start: parseTime(m[1].replace(',', '.')),
            end: parseTime(m[2].replace(',', '.')),
            text: lines.slice(j + 1).join('\n').trim()
          });
          break;
        }
      }
    }
    cues.sort(function (a, b) { return a.start - b.start; });
    return cues;
  }

  // ---- overlay renderer ----
  var V = null, box = null, cues = [], style = {}, raf = 0, lastIdx = -1;

  function applyStyle() {
    if (!box || !V) return;
    var h = V.clientHeight || 720;
    var fs = Math.round(h * (style.fontPct / 100));
    var o = style.outlinePx, oc = style.outline;
    box.style.cssText = [
      'position:absolute', 'left:0', 'right:0', 'text-align:center',
      'bottom:' + style.bottomPct + '%',
      'font-family:' + style.fontFamily,
      'font-size:' + fs + 'px',
      'font-weight:' + (style.bold ? '700' : '400'),
      'color:' + style.color,
      'line-height:1.25', 'pointer-events:none', 'z-index:2147483647',
      'white-space:pre-line', 'padding:0 6%',
      'text-shadow:' + [
        o + 'px ' + o + 'px ' + o + 'px ' + oc,
        '-' + o + 'px -' + o + 'px ' + o + 'px ' + oc,
        o + 'px -' + o + 'px ' + o + 'px ' + oc,
        '-' + o + 'px ' + o + 'px ' + o + 'px ' + oc
      ].join(','),
      'background:' + (style.bgAlpha > 0 ? 'rgba(0,0,0,' + style.bgAlpha + ')' : 'transparent')
    ].join(';');
  }

  function mount(videoEl, userStyle) {
    V = videoEl;
    style = Object.assign({}, DEFAULT_STYLE, userStyle || {});
    if (box) box.remove();
    box = document.createElement('div');
    box.id = 'nfsubs-overlay';
    var parent = V.parentElement || document.body;
    if (getComputedStyle(parent).position === 'static') parent.style.position = 'relative';
    parent.appendChild(box);
    applyStyle();
    window.addEventListener('resize', applyStyle);
  }

  function setCues(c) { cues = c || []; lastIdx = -1; }
  function setStyle(s) { Object.assign(style, s || {}); applyStyle(); }

  function tick() {
    if (V && box) {
      var t = V.currentTime, txt = '';
      // linear-ish scan from lastIdx for efficiency
      for (var i = 0; i < cues.length; i++) {
        if (cues[i].start <= t && t < cues[i].end) { txt = cues[i].text; break; }
        if (cues[i].start > t) break;
      }
      if (box.__t !== txt) { box.textContent = txt; box.__t = txt; }
    }
    raf = requestAnimationFrame(tick);
  }
  function start() { if (!raf) tick(); }
  function stop() { if (raf) { cancelAnimationFrame(raf); raf = 0; } if (box) box.textContent = ''; }

  window.NFSubs = {
    parseTTML: parseTTML, parseVTT: parseVTT,
    mount: mount, setCues: setCues, setStyle: setStyle, start: start, stop: stop,
    DEFAULT_STYLE: DEFAULT_STYLE
  };
})();
