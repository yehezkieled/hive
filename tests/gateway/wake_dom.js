// A minimal DOM, just enough to run the desk's page script with an open firstmate popup.
// Usage: node wake_dom.js <script.js> <answer as JSON>
// The popup holds the note of an earlier try and an armed Wake form. Sends the confirming tap,
// answers it with the given JSON, and reports what the popup shows afterwards.
'use strict';
const fs = require('fs');
const [, , scriptPath, answerJson] = process.argv;

class El {
  constructor(tag, attrs = {}, classes = [], text = '') {
    this.tagName = tag.toUpperCase();
    this.attrs = { ...attrs };
    this.classes = new Set(classes);
    this.childNodes = [];
    this.parentNode = null;
    this.textContent = text;
    this.title = '';
    const self = this;
    this.classList = {
      add: (...c) => c.forEach((x) => self.classes.add(x)),
      remove: (...c) => c.forEach((x) => self.classes.delete(x)),
      contains: (c) => self.classes.has(c),
    };
  }
  get id() { return this.attrs.id || ''; }
  set className(v) { this.classes = new Set(String(v).split(' ').filter(Boolean)); }
  get className() { return [...this.classes].join(' '); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  hasAttribute(k) { return k in this.attrs; }
  removeAttribute(k) { delete this.attrs[k]; }
  appendChild(c) { c.parentNode = this; this.childNodes.push(c); return c; }
  removeChild(c) { this.childNodes.splice(this.childNodes.indexOf(c), 1); c.parentNode = null; return c; }
  contains(n) { for (; n; n = n.parentNode) if (n === this) return true; return false; }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => n.childNodes.forEach((c) => { if (matches(c, sel, this)) out.push(c); walk(c); });
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getElementsByTagName(t) { return this.querySelectorAll(t); }
}

// One compound of tag, #id and .class; anything else in the page script matches nothing here.
function simple(n, s) {
  const m = /^([a-z]+)?(#[\w-]+)?((?:\.[\w-]+)*)$/.exec(s);
  if (!m || !s) return false;
  if (m[1] && n.tagName !== m[1].toUpperCase()) return false;
  if (m[2] && n.id !== m[2].slice(1)) return false;
  return m[3].split('.').filter(Boolean).every((c) => n.classes.has(c));
}
function matches(n, sel, root) {
  return sel.split(',').some((one) => {
    const parts = one.trim().split(/\s+/);
    if (!simple(n, parts.pop())) return false;
    let up = n.parentNode;
    while (parts.length) {
      const want = parts.pop();
      while (up && up !== root && !simple(up, want)) up = up.parentNode;
      if (!up || up === root) return false;
      up = up.parentNode;
    }
    return true;
  });
}

const body = new El('body');
const chip = body.appendChild(new El('details', { id: 'fmchip' }, ['qwrap']));
chip.open = true;
const pop = chip.appendChild(new El('div', {}, ['qpop', 'fmpop']));
pop.appendChild(new El('p', {}, ['fmpop__try'], 'Last try, just now: Firstmate started in pane w1:p1.'));
const form = pop.appendChild(new El('form', { action: '/act/wake', 'data-step': 'tok', 'data-rid': 'web-1', 'data-extra': '{}' }));
const button = form.appendChild(new El('button', { 'data-was': 'Wake firstmate' }, ['btn', 'is-armed'], 'Tap again'));
form.elements = { rid: { value: 'web-1' } };
form.action = '/act/wake';
button.name = '';
button.value = '';
form.acBtn = '=';  // the armed button, as the first tap left it
form.acEl = button;

const listeners = {};
const doc = {
  hidden: false,
  body,
  activeElement: null,
  documentElement: { style: { setProperty() {} }, scrollHeight: 0 },
  getElementById: (id) => body.querySelector('#' + id),
  querySelector: (s) => body.querySelector(s),
  querySelectorAll: (s) => body.querySelectorAll(s),
  getElementsByTagName: (t) => body.querySelectorAll(t),
  createElement: (t) => new El(t),
  addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
};
Object.defineProperty(globalThis, 'navigator', {
  value: { userAgent: 'Mozilla/5.0 (X11; Linux)', platform: 'Linux', maxTouchPoints: 0 },
  configurable: true,
});
Object.assign(globalThis, {
  document: doc,
  window: globalThis,
  location: { href: '/' },
  matchMedia: () => ({ matches: false }),
  setInterval: () => 0,
  setTimeout: () => 0,
  FormData: function () { return [['csrf', 'x'], ['rid', 'web-1']]; },
  fetch: (url) => (url === '/act/wake'
    ? Promise.resolve({ status: 502, json: () => Promise.resolve(JSON.parse(answerJson)) })
    : new Promise(() => {})),
});

const settle = () => new Promise((r) => setImmediate(r));
(async () => {
  new Function(fs.readFileSync(scriptPath, 'utf8'))();
  await settle();
  const ev = { target: form, submitter: button, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
  (listeners.submit || []).forEach((fn) => fn(ev));
  for (let i = 0; i < 5; i++) await settle();
  const texts = (sel) => chip.querySelectorAll(sel).map((n) => n.textContent);
  process.stdout.write(JSON.stringify({ tries: texts('.fmpop__try'), errors: texts('.act-err'), button: button.textContent }));
})();
