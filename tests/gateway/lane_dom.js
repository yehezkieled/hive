// A minimal DOM, just enough to run the desk's page script against a Needs-you lane.
// Usage: node lane_dom.js <script.js> <reduce|motion> <steps as JSON> [lane as JSON]
// A step is [position, ms]: a click on whichever item sits at that position in the lane at that
// moment; or ["refresh"]: a live `desk` event that re-renders the page as the server sent it.
// The lane is [[id, project, held], ...]; by default three bnm items with the first held.
'use strict';
const fs = require('fs');
const [, , scriptPath, motion, clicksJson, laneJson] = process.argv;
const spec = laneJson
  ? JSON.parse(laneJson)
  : [['ask', 'bnm', true], ['later-email', 'bnm', false], ['plain', 'bnm', false]];

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
  get textContent() { return ''; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  append(child) { child.parentNode = this; this.childNodes.push(child); return child; }
  insertBefore(child, ref) {
    if (child === ref) return child;
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
const body = doc.append(new Node('body', { 'data-refresh': '30000' }));
const main = body.append(new Node('main'));
let lane;
let renders = 0;
function render() {
  main.childNodes.slice().forEach((n) => main.removeChild(n));
  lane = main.append(new Node('div', { 'data-group': 'waiting' }));
  for (const [id, project, primary] of spec) {
    const href = '/p/' + project;
    const it = lane.append(new Node('div', { 'data-item': id, 'data-open': href }, primary ? ['nyx', 'is-primary'] : ['nyx']));
    it.append(new Node('a', { href }, ['nyx__head']));
  }
  renders += 1;
}
render();
Object.defineProperty(main, 'textContent', { get: () => 'render ' + renders });
Object.defineProperty(main, 'innerHTML', { get: () => '', set: render });
const page = { getElementById: () => null, querySelector: (s) => (s === 'main' ? { textContent: 'render ' + (renders + 1), innerHTML: '' } : null) };
const events = {};
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
  scrollY: 0,
  scrollTo: () => {},
  fetch: () => Promise.resolve({ ok: true, text: () => Promise.resolve('<html>') }),
  DOMParser: class { parseFromString() { return page; } },
  EventSource: class { addEventListener(type, fn) { events[type] = fn; } },
});
Date.now = () => now;
new Function(fs.readFileSync(scriptPath, 'utf8'))();

const order = () => lane.querySelectorAll('.nyx').map((n) => n.getAttribute('data-item'));
const primary = () => lane.querySelector('.nyx.is-primary').getAttribute('data-item');
const project = (n) => n.getAttribute('data-open');
const steps = [];
(async () => {
for (const [pos, ms] of JSON.parse(clicksJson)) {
  if (pos === 'refresh') {
    events.desk();
    for (let i = 0; i < 10; i++) await Promise.resolve();
    const it = lane.querySelector('.nyx.is-primary');
    steps.push({ order: order(), primary: it && it.getAttribute('data-item'), project: it && project(it), opened: opened.slice() });
    continue;
  }
  now += ms;
  const head = lane.querySelectorAll('.nyx')[pos].querySelector('.nyx__head');
  let prevented = false;
  for (const fn of listeners.click || []) fn({ target: head, button: 0, preventDefault: () => { prevented = true; } });
  lane.querySelectorAll('.nyx').forEach((n) => { if (n.style.transform || n.classes.has('is-flip')) transformed.add(n.getAttribute('data-item')); });
  steps.push({ order: order(), primary: primary(), project: project(lane.querySelector('.nyx.is-primary')), opened: opened.slice(), prevented });
}
process.stdout.write(JSON.stringify({ steps, animated: [...transformed].sort() }));
})();
