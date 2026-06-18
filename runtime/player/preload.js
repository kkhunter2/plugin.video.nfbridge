/*
 * preload — injects the subtitle module + controller into the Netflix page
 * (main world, bypassing page CSP via webFrame.executeJavaScript).
 */
const { webFrame } = require('electron');
const fs = require('fs');
const path = require('path');

function inject(file) {
  try {
    webFrame.executeJavaScript(fs.readFileSync(path.join(__dirname, file), 'utf8'));
  } catch (e) { /* noop */ }
}

inject('subtitles.js');   // defines window.NFSubs
inject('inject.js');      // page-side controller wires NFSubs to the <video>
