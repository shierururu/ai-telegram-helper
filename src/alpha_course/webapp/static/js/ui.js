// Примитивы интерфейса: h(), иконки, листы, тосты.
import { tg } from './tg.js';

const SVG_TAGS = new Set(['svg', 'path', 'circle', 'rect', 'line', 'g']);
const PROPS = new Set(['value', 'checked', 'disabled', 'hidden', 'textContent', 'htmlFor', 'tabIndex', 'readOnly']);

export function h(tag, attrs, ...kids) {
  const svg = SVG_TAGS.has(tag);
  const el = svg ? document.createElementNS('http://www.w3.org/2000/svg', tag) : document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.setAttribute('class', v);
      else if (k === 'style') {
        for (const [sk, sv] of Object.entries(v)) sk.startsWith('--') ? el.style.setProperty(sk, sv) : (el.style[sk] = sv);
      } else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (!svg && PROPS.has(k)) el[k] = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
  }
  add(el, kids);
  return el;
}
function add(el, kids) {
  for (const k of kids) {
    if (k == null || k === false) continue;
    if (Array.isArray(k)) add(el, k);
    else el.append(k.nodeType ? k : document.createTextNode(String(k)));
  }
}
export const $ = (sel, root = document) => root.querySelector(sel);
export function clear(el) { el.replaceChildren(); return el; }

const ICONS = {
  home: '<path d="M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4v-5h-6v5H5a1 1 0 0 1-1-1z"/>',
  chat: '<path d="M5 6a3 3 0 0 1 3-3h8a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3h-5l-4 4v-4H8a3 3 0 0 1-3-3z"/>',
  route: '<circle cx="6" cy="18" r="2"/><circle cx="18" cy="6" r="2"/><path d="M8 18h7a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h7"/>',
  calendar: '<rect x="4" y="5" width="16" height="15" rx="3"/><path d="M8 3v4M16 3v4M4 10h16"/>',
  more: '<circle cx="5.5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="18.5" cy="12" r="1.4"/>',
  back: '<path d="M15 5l-7 7 7 7"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 21h4"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.5-6 8-6s8 2 8 6"/>',
  sliders: '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
  send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  ext: '<path d="M14 5h5v5M19 5l-8 8M18 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  chev: '<path d="M9 5l7 7-7 7"/>',
  copy: '<rect x="8" y="8" width="11" height="12" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
  up: '<path d="M7 11v9H4v-9zM7 11l4-7c1.6 0 2.6 1.1 2.1 3L12.6 10H18a2 2 0 0 1 2 2.3l-1 5.5A2 2 0 0 1 17 20H7"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16l.7 1.8L21.5 18.5l-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7z"/>',
  wallet: '<rect x="3" y="6" width="18" height="13" rx="3"/><path d="M3 10h18M16 14.5h2"/>',
  doc: '<path d="M7 3h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  repeat: '<path d="M17 4l3 3-3 3"/><path d="M4 11v-1a3 3 0 0 1 3-3h13"/><path d="M7 20l-3-3 3-3"/><path d="M20 13v1a3 3 0 0 1-3 3H4"/>',
  trash: '<path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  gem: '<path d="M6 4h12l3 5-9 11L3 9z"/><path d="M3 9h18M9 4l-2 5 5 11 5-11-2-5"/>',
  bulb: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
  alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14-4L4 9M4 4v5h5M4 13a8 8 0 0 0 14 4l2-2M20 20v-5h-5"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>',
  flame: '<path d="M12 3c.8 3.2 5 5 5 10a5 5 0 0 1-10 0c0-2 .8-3.4 2-4.5 0 2 .8 3 2 3-.6-3-.4-5.6 1-8.5z"/>',
  word: '<rect x="4" y="4" width="7" height="7" rx="1.8"/><rect x="13" y="4" width="7" height="7" rx="1.8"/><rect x="4" y="13" width="7" height="7" rx="1.8"/><path d="M13 16.5h7"/>',
  share: '<path d="M12 15V4M8 8l4-4 4 4M5 13v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5"/>',
  chart: '<path d="M5 20V11M12 20V4M19 20v-6"/>',
  games: '<rect x="3" y="7" width="18" height="11" rx="4"/><path d="M8 10v5M5.5 12.5h5M15.5 11.5h.01M18 14h.01"/>',
  users: '<circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M3 19c0-3 3-5 6-5s6 2 6 5M15 14.5c3 0 6 1.5 6 4.5"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17h.01"/>',
  arrowr: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  tools: '<rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><path d="M16.5 13v7M13 16.5h7"/>',
  calc: '<rect x="5" y="3" width="14" height="18" rx="3"/><path d="M8 7h8M8.5 12h.01M12 12h.01M15.5 12h.01M8.5 16h.01M12 16h.01M15.5 16h.01"/>',
  download: '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19h14"/>',
  book: '<path d="M5 5a2 2 0 0 1 2-2h12v15H7a2 2 0 0 0-2 2z"/><path d="M5 20a2 2 0 0 0 2 2h12v-4"/>',
};
export function icon(name, size = 22, extra = '') {
  const s = h('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false', class: extra });
  s.innerHTML = ICONS[name] || '';
  return s;
}

export function haptic(k) { tg.haptic(k); }

// ---------- тосты ----------
export function toast(text, ms = 3200) {
  const box = document.getElementById('toasts');
  const el = h('div', { class: 'toast' }, text);
  box.append(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 220); }, ms);
}

// ---------- листы ----------
const layer = () => document.getElementById('layer');
let openCount = 0;

export function openSheet({ title, build, onClose }) {
  const prev = document.activeElement;
  const scrim = h('div', { class: 'scrim' });
  const titleId = 'sh' + Math.random().toString(36).slice(2, 7);
  const body = h('div', { class: 'sb' });
  const sheet = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': titleId },
    h('div', { class: 'grab' }),
    h('div', { class: 'sh' },
      h('h2', { id: titleId }, title || ''),
      h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Закрыть', onclick: () => close() }, icon('close', 22))),
    body);
  let closed = false;
  let popBack = () => {};
  const api = { body, sheet, close, setTitle: (t) => { $('h2', sheet).textContent = t; } };
  function close(result) {
    if (closed) return; closed = true;
    popBack(); document.removeEventListener('keydown', onKey);
    scrim.classList.add('out'); sheet.classList.add('out');
    setTimeout(() => {
      scrim.remove(); sheet.remove();
      if (--openCount === 0) document.documentElement.classList.remove('no-scroll');
      try { prev?.focus?.({ preventScroll: true }); } catch (e) { /* noop */ }
      onClose?.(result);
    }, 220);
  }
  function onKey(e) {
    if (e.key === 'Escape') { close(); return; }
    if (e.key !== 'Tab') return;
    const f = [...sheet.querySelectorAll('button,a[href],input,textarea,select,[tabindex]:not([tabindex="-1"])')].filter((x) => !x.disabled && x.offsetParent !== null);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
  scrim.addEventListener('click', () => close());
  document.addEventListener('keydown', onKey);
  popBack = tg.pushBack(() => close());
  if (openCount++ === 0) document.documentElement.classList.add('no-scroll');
  layer().append(scrim, sheet);
  body.append(build(close, api));
  const f = sheet.querySelector('.sb input, .sb textarea');
  setTimeout(() => (f || sheet.querySelector('.icon-btn'))?.focus?.({ preventScroll: true }), 60);
  return api;
}

export function confirmSheet({ title, text, confirm = 'Подтвердить', cancel = 'Отмена', danger = false }) {
  return new Promise((resolve) => {
    let result = false;
    openSheet({
      title,
      onClose: () => resolve(result),
      build: (close) => h('div', { class: 'stack' },
        text ? h('p', { class: 'muted' }, text) : null,
        h('div', { class: 'actions' },
          h('button', { class: 'btn soft grow', type: 'button', onclick: () => close() }, cancel),
          h('button', { class: `btn grow ${danger ? 'danger solid' : 'primary'}`, type: 'button', onclick: () => { result = true; haptic(danger ? 'warning' : 'success'); close(); } }, confirm))),
    });
  });
}

// кнопка с индикатором выполнения; fn может вернуть промис
export function busyButton(btn, fn) {
  let busy = false;
  btn.addEventListener('click', async (e) => {
    if (busy) return; busy = true;
    const old = [...btn.childNodes]; btn.disabled = true; btn.replaceChildren(h('span', { class: 'spin' }));
    try { await fn(e); } finally { busy = false; btn.disabled = false; btn.replaceChildren(...old); }
  });
  return btn;
}
