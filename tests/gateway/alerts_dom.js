// A minimal DOM, just enough to run the desk's page script with the usage menu and alerts button.
// Usage: node alerts_dom.js <script.js> <ios|desktop> <push|nopush>
// Loads the page, taps Alerts against a server without push keys, and reports the menu state.
'use strict';
const fs = require('fs');
const [, , scriptPath, platform, push] = process.argv;

class El {
  constructor(id) {
    this.id = id;
    this.open = false;
    this.hidden = true;
    this.textContent = '';
    this.attrs = {};
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return k in this.attrs; }
  contains(n) { return n === this; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  getElementsByTagName() { return []; }
}

const els = { qchip: new El('qchip'), alerts: new El('alerts'), 'alerts-note': new El('alerts-note') };
const listeners = {};
const doc = {
  hidden: false,
  body: new El('body'),
  documentElement: { style: { setProperty() {} } },
  getElementById: (id) => els[id] || null,
  querySelector: () => null,
  querySelectorAll: () => [],
  getElementsByTagName: () => [],
  addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
};
const nav = {
  userAgent: platform === 'ios' ? 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)' : 'Mozilla/5.0 (X11; Linux)',
  platform: platform === 'ios' ? 'iPhone' : 'Linux',
  maxTouchPoints: 0,
};
const reg = { pushManager: { getSubscription: () => Promise.resolve(null) } };
if (push === 'push') nav.serviceWorker = { register: () => Promise.resolve(reg) };
Object.defineProperty(globalThis, 'navigator', { value: nav, configurable: true });
Object.assign(globalThis, {
  document: doc,
  window: globalThis,
  location: { href: '/' },
  matchMedia: () => ({ matches: false }),
  setInterval: () => 0,
  setTimeout: () => 0,
  fetch: (url) => (url === '/push/key'
    ? Promise.resolve({ json: () => Promise.resolve({ enabled: false, csrf: 'x' }) })
    : new Promise(() => {})),
});
if (push === 'push') Object.assign(globalThis, { PushManager: function () {}, Notification: { requestPermission: () => Promise.resolve('denied') } });

const settle = () => new Promise((r) => setImmediate(r));
(async () => {
  new Function(fs.readFileSync(scriptPath, 'utf8'))();
  await settle();
  const loaded = { open: els.qchip.open, note: els['alerts-note'].textContent };
  if (els.alerts.onclick) { els.alerts.onclick(); for (let i = 0; i < 5; i++) await settle(); }
  const tapped = { open: els.qchip.open, note: els['alerts-note'].textContent };
  process.stdout.write(JSON.stringify({ loaded, tapped }));
})();
