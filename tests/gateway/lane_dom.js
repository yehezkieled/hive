// A minimal DOM, just enough to run the desk's page script against a Needs-you lane.
// Usage: node lane_dom.js <script.js> <reduce|motion> <clicks as JSON [[position, ms], ...]>
// A click lands on whichever item sits at that position in the lane at that moment.
'use strict';
const fs = require('fs');
const [, , scriptPath, motion, clicksJson] = process.argv;

class Node {
  constructor(tag, attrs = {}, classes = []) {
    this.tagName = tag.toUpperCase();
    this.attrs = { ...attrs };
    this.classes = new Set(classes);
    this.childNodes = [];
    this.parentNode = null;
    this.style = { transition: '', transform: '' };
    const self = this;
    this.classList = {
      add: (...c) => c.forEach((x) => self.classes.add(x)),
      remove: (...c) => c.forEach((x) => self.classes.delete(x)),
      contains: (c) => self.classes.has(c),
    };
  }
  get offsetHeight() { return 0; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  append(child) { child.parentNode = this; this.childNodes.push(child); return child; }
  insertBefore(child, ref) {
    if (child.parentNode) child.parentNode.removeChild(child);
    const i = ref ? this.childNodes.indexOf(ref) : this.childNodes.length;
    this.childNodes.splice(i, 0, child);
    child.parentNode = this;
    return child;
  }
  removeChild(child) {
    this.childNodes.splice(this.childNodes.indexOf(child), 1);
    child.parentNode = null;
    return child;
  }
  get firstChild() { return this.childNodes[0] || null; }
  contains(n) { for (; n; n = n.parentNode) if (n === this) return true; return false; }
  matches(sel) { return sel.split(',').some((s) => simple(this, s.trim())); }
  closest(sel) { for (let n = this; n instanceof Node; n = n.parentNode) if (n.matches(sel)) return n; return null; }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => n.childNodes.forEach((c) => { if (c instanceof Node) { if (c.matches(sel)) out.push(c); walk(c); } });
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getElementsByTagName(t) { return this.querySelectorAll(t); }
  getBoundingClientRect() {
    const sibs = this.parentNode.childNodes.filter((c) => c instanceof Node);
    return { top: sibs.indexOf(this) * 40 };
  }
}

function simple(n, s) {
  const m = /^([a-z]*)((?:\.[\w-]+)*)((?:\[[\w-]+\])*)$/i.exec(s);
  if (!m) return false;
  if (m[1] && n.tagName !== m[1].toUpperCase()) return false;
  if (m[2] && !m[2].slice(1).split('.').every((c) => n.classes.has(c))) return false;
  return (m[3].match(/[\w-]+/g) || []).every((a) => a in n.attrs);
}

const doc = new Node('html');
const body = doc.append(new Node('body'));
const lane = body.append(new Node('div', { 'data-group': 'bnm', 'data-open': '/p/bnm' }));
for (const [id, primary] of [['ask', true], ['later-email', false], ['plain', false]]) {
  const it = lane.append(new Node('div', { 'data-item': id }, primary ? ['nyx', 'is-primary'] : ['nyx']));
  it.append(new Node('a', { href: '/p/bnm' }, ['nyx__head']));
}
const listeners = {};
Object.assign(doc, {
  body,
  documentElement: doc,
  activeElement: null,
  hidden: false,
  getElementById: () => null,
  createComment: () => ({ comment: true, parentNode: null }),
  addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
});

let now = 1000;
const opened = [];
const transformed = new Set();
const location = {};
Object.defineProperty(location, 'href', { get: () => '/', set: (v) => opened.push(v) });
Object.assign(globalThis, {
  document: doc,
  window: globalThis,
  location,
  matchMedia: () => ({ matches: motion === 'motion' }),
  setInterval: () => 0,
  setTimeout: () => 0,
  fetch: () => new Promise(() => {}),
});
Date.now = () => now;
new Function(fs.readFileSync(scriptPath, 'utf8'))();

const order = () => lane.querySelectorAll('.nyx').map((n) => n.getAttribute('data-item'));
const primary = () => lane.querySelector('.nyx.is-primary').getAttribute('data-item');
const steps = [];
for (const [pos, ms] of JSON.parse(clicksJson)) {
  now += ms;
  const head = lane.querySelectorAll('.nyx')[pos].querySelector('.nyx__head');
  let prevented = false;
  for (const fn of listeners.click || []) fn({ target: head, button: 0, preventDefault: () => { prevented = true; } });
  lane.querySelectorAll('.nyx').forEach((n) => { if (n.style.transform || n.classes.has('is-flip')) transformed.add(n.getAttribute('data-item')); });
  steps.push({ order: order(), primary: primary(), opened: opened.slice(), prevented });
}
process.stdout.write(JSON.stringify({ steps, animated: [...transformed].sort() }));
