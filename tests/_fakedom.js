// A small fake DOM shared by the runtime tests that load a page's real script.
// Not a browser: just enough elements, ids, classes and events to drive a handler
// and read back what it drew.
'use strict';

class El {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = '';
    this.dataset = {};
    this.style = {};
    this.children = [];
    this.parentNode = null;
    this._text = '';
    this._attrs = {};
    this._listeners = {};
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.className = '';
    this.type = '';
    this.readOnly = false;
    const self = this;
    this.classList = {
      toggle(name, on) {
        const names = new Set(self.className.split(/\s+/).filter(Boolean));
        if (on === undefined ? !names.has(name) : on) names.add(name); else names.delete(name);
        self.className = Array.from(names).join(' ');
      },
      add(name) { self.classList.toggle(name, true); },
      remove(name) { self.classList.toggle(name, false); },
      contains(name) { return self.className.split(/\s+/).includes(name); },
    };
  }
  get textContent() {
    return this._text + this.children.map((c) => c.textContent).join('');
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  append(...items) {
    items.forEach((i) => this.appendChild(typeof i === 'string' ? Object.assign(new El('#text'), {_text: i}) : i));
  }
  replaceChildren(...items) { this.children = []; this._text = ''; this.append(...items); }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  async fire(type, extra) {
    const event = Object.assign({target: this, preventDefault() {}, key: ''}, extra || {});
    for (const fn of this._listeners[type] || []) await fn.call(this, event);
  }
  setAttribute(k, v) { this._attrs[k] = String(v); }
  getAttribute(k) { return this._attrs[k] ?? null; }
  focus() {}
  select() {}
  closest(selector) {
    for (let n = this; n; n = n.parentNode) {
      if (selector.split(',').some((s) => n._matches(s))) return n;
    }
    return null;
  }
  _matches(sel) {
    sel = sel.trim();
    let m;
    if ((m = /^\.([\w-]+)$/.exec(sel))) return this.classList.contains(m[1]);
    if ((m = /^([a-z]+)\[type=(\w+)\]$/i.exec(sel))) return this.tagName === m[1].toUpperCase() && this.type === m[2];
    if ((m = /^\[([\w-]+)\]$/.exec(sel))) {
      const key = m[1].replace(/^data-/, '').replace(/-(\w)/g, (_a, c) => c.toUpperCase());
      return key in this.dataset;
    }
    if ((m = /^([a-z]+)$/i.exec(sel))) return this.tagName === m[1].toUpperCase();
    return false;
  }
  querySelectorAll(selector) {
    const parts = selector.split(',');
    const out = [];
    const walk = (node) => {
      node.children.forEach((c) => {
        if (parts.some((p) => c._matches(p))) out.push(c);
        walk(c);
      });
    };
    walk(this);
    return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}


module.exports = {El};
