/*
 * inject.js — page-world subtitle controller (runs inside netflix.com).
 *
 * Netflix downloads/decodes subtitles off the main thread (Web Worker / MSE), so a
 * page-level fetch hook can't see the TTML/VTT. But Netflix *renders* the active cue
 * into a DOM node (.player-timedtext). So we MIRROR that already-rendered text into
 * our own styled overlay (NFSubs) and hide Netflix's — robust, always in sync.
 *
 * Native subs are hidden via a persistent `!important` stylesheet (NOT by polling
 * element.style, which races Netflix's re-render and flickers). opacity:0 (not
 * display:none) keeps the node in layout so its innerText stays readable.
 */
(function () {
  'use strict';
  if (!window.NFSubs) return;

  var STYLE = window.__NFSUBS_STYLE || window.NFSubs.DEFAULT_STYLE;
  var overlay = null;

  // hide Netflix's own subtitle rendering once, persistently
  if (!document.getElementById('nfsubs-hide')) {
    var st = document.createElement('style');
    st.id = 'nfsubs-hide';
    st.textContent = '.player-timedtext{opacity:0 !important;}';
    (document.head || document.documentElement).appendChild(st);
  }

  function ensureOverlay() {
    var v = document.querySelector('video');
    if (v && !overlay) {
      window.NFSubs.mount(v, STYLE);                 // creates the styled #nfsubs-overlay box
      overlay = document.getElementById('nfsubs-overlay');
    }
    return overlay;
  }

  function nativeNode() {
    return document.querySelector('.player-timedtext-text-container') ||
           document.querySelector('.player-timedtext');
  }

  function loop() {
    var o = ensureOverlay();
    if (!o) return;
    var n = nativeNode();
    var text = n ? (n.innerText || n.textContent || '').trim() : '';
    if (o.__t !== text) { o.textContent = text; o.__t = text; }
  }

  setInterval(loop, 120);
})();
