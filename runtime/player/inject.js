/*
 * inject.js — page-world controller (runs inside netflix.com).
 *
 * 1. Hooks fetch/XHR to capture Netflix's (un-encrypted) TTML/VTT subtitle files.
 * 2. Finds the <video>, mounts the NFSubs overlay, starts the currentTime sync.
 * 3. Hides Netflix's own subtitle DOM so only our styled overlay shows.
 *
 * Subtitle files are plain text (not DRM); we content-sniff responses rather than
 * relying on brittle URL patterns.
 */
(function () {
  'use strict';
  if (!window.NFSubs) return;

  var STYLE = window.NFSubs.DEFAULT_STYLE; // overridable via window.__NFSUBS_STYLE

  function looksLikeSub(text) {
    var head = (text || '').slice(0, 40);
    if (/^\s*<\?xml/.test(head) && /ttml|<tt[\s>]/.test(text.slice(0, 400))) return 'ttml';
    if (/^\s*<tt[\s>]/.test(head)) return 'ttml';
    if (/^\s*WEBVTT/.test(head)) return 'vtt';
    return null;
  }

  function ingest(text) {
    try {
      var kind = looksLikeSub(text);
      if (!kind) return false;
      var cues = kind === 'ttml' ? window.NFSubs.parseTTML(text) : window.NFSubs.parseVTT(text);
      if (cues && cues.length) { window.NFSubs.setCues(cues); window.NFSubs.start(); return true; }
    } catch (e) { /* ignore parse errors */ }
    return false;
  }

  // ---- fetch hook ----
  var _fetch = window.fetch;
  window.fetch = function (input, init) {
    var p = _fetch.apply(this, arguments);
    try {
      p.then(function (resp) {
        var ct = (resp.headers && resp.headers.get && resp.headers.get('content-type')) || '';
        if (/xml|ttml|vtt|text\/plain/.test(ct) || true) {
          resp.clone().text().then(ingest).catch(function () {});
        }
      }).catch(function () {});
    } catch (e) {}
    return p;
  };

  // ---- XHR hook ----
  var _open = XMLHttpRequest.prototype.open, _send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function () { return _open.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    var xhr = this;
    xhr.addEventListener('load', function () {
      try { if (typeof xhr.responseText === 'string') ingest(xhr.responseText); } catch (e) {}
    });
    return _send.apply(this, arguments);
  };

  // ---- attach to the <video> + hide Netflix's own subs ----
  function hideNativeSubs() {
    document.querySelectorAll('.player-timedtext, [data-uia="player-timedtext"]').forEach(function (el) {
      el.style.display = 'none';
    });
  }

  var mounted = false;
  var poll = setInterval(function () {
    var v = document.querySelector('video');
    if (v && !mounted) {
      mounted = true;
      window.NFSubs.mount(v, window.__NFSUBS_STYLE || STYLE);
      window.NFSubs.start();
    }
    hideNativeSubs();
  }, 1000);

  new MutationObserver(hideNativeSubs).observe(document.documentElement, { childList: true, subtree: true });

  window.addEventListener('beforeunload', function () { clearInterval(poll); });
})();
