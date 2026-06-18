/*
 * Embedded player — Electron main process.
 * Loads the Widevine component (VMP privacy mode), optionally injects a Netflix
 * session (nf_cookies.json passed by the addon), and opens netflix.com/watch.
 * The renderer-side subtitle re-render is wired by preload.js.
 *
 * Run with: NF_URL="https://www.netflix.com/watch/<id>" electron .
 */
const { app, BrowserWindow, components, session } = require('electron');
const path = require('path');
const fs = require('fs');

app.commandLine.appendSwitch('enable-features', 'HardwareSecureDecryption');

// Present as a Netflix-supported browser (Chrome). The default Electron UA carries
// an "Electron/.." + app-name token that Netflix rejects as an unsupported browser
// (error M7121). DRM still flows through the real Widevine CDM — this only changes
// how the client identifies itself.
const CHROME_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
                  '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36';
app.userAgentFallback = CHROME_UA;

async function injectCookies() {
  const p = path.join(__dirname, 'nf_cookies.json');
  if (!fs.existsSync(p)) return;
  try {
    const cookies = JSON.parse(fs.readFileSync(p, 'utf-8'));
    for (const c of cookies) {
      const domain = (c.domain || '.netflix.com').replace(/^\./, '');
      try {
        await session.defaultSession.cookies.set({
          url: 'https://' + domain + (c.path || '/'),
          name: c.name, value: c.value, domain: c.domain, path: c.path || '/',
          secure: c.secure !== false, httpOnly: !!c.httpOnly,
          expirationDate: c.expirationDate || undefined
        });
      } catch (e) { /* ignore individual cookie */ }
    }
  } catch (e) { /* no session provided */ }
}

app.whenReady().then(async () => {
  try { await components.whenReady(); } catch (e) { /* widevine optional for browse */ }
  await injectCookies();

  const win = new BrowserWindow({
    width: 1600, height: 900,
    fullscreen: process.env.NF_FULLSCREEN === '1',
    autoHideMenuBar: true,
    backgroundColor: '#000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: false,   // preload shares page world to drive the overlay
      nodeIntegration: false,
      sandbox: false
    }
  });

  const url = process.env.NF_URL || 'https://www.netflix.com/';
  win.loadURL(url);
});

app.on('window-all-closed', () => app.quit());
