// Общие компоненты: материалы, состояния, переключатели, кольцо прогресса.
import { h, icon, haptic } from './ui.js';
import { tg } from './tg.js';
import { api } from './api.js';

export function pageHead(eyebrow, title, sub) {
  return h('div', { class: 'page-head' },
    eyebrow ? h('div', { class: 'eyebrow' }, eyebrow) : null,
    h('h1', { class: 'h1' }, title),
    sub ? h('p', { class: 'muted' }, sub) : null);
}

export function section(title, right) {
  return h('div', { class: 'sec' }, h('h2', { style: { font: 'inherit' } }, title), right || null);
}

export function segmented(items, value, onChange, label) {
  const wrap = h('div', { class: 'seg', role: 'group', 'aria-label': label || null });
  const draw = (v) => {
    wrap.replaceChildren(...items.map(([val, text]) => h('button', {
      type: 'button', 'aria-pressed': String(val === v),
      onclick: () => { if (val === value) return; haptic('select'); value = val; draw(val); onChange(val); },
    }, text)));
  };
  draw(value);
  wrap.set = (nv) => { value = nv; draw(nv); };
  return wrap;
}

// выбор из списка значений; allowClear добавляет «Не указано»
export function chipsPick(opts, value, onPick, { labelOf = (x) => x, required = false } = {}) {
  const box = h('div', { class: 'chips' });
  const draw = () => box.replaceChildren(...opts.map((o) => {
    const val = Array.isArray(o) ? o[0] : o; const text = Array.isArray(o) ? o[1] : labelOf(o);
    return h('button', { class: 'chip' + (val === value ? ' on' : ''), type: 'button', 'aria-pressed': String(val === value),
      onclick: () => { value = val === value && !required ? null : val; haptic('select'); draw(); onPick(value); } }, text);
  }));
  draw();
  return box;
}

export function switchBtn(checked, onChange, label) {
  const b = h('button', { class: 'switch', type: 'button', role: 'switch', 'aria-checked': String(!!checked), 'aria-label': label });
  b.addEventListener('click', async () => {
    const next = b.getAttribute('aria-checked') !== 'true';
    b.setAttribute('aria-checked', String(next)); haptic('select');
    try { await onChange(next); } catch (e) { b.setAttribute('aria-checked', String(!next)); }
  });
  return h('span', { class: 'switch-hit' }, b);
}

export function ring(done, total, size = 56, stroke = 5, text) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r;
  const p = total ? Math.min(1, done / total) : 0;
  const bar = h('circle', { class: 'bar', cx: size / 2, cy: size / 2, r, fill: 'none', 'stroke-width': stroke, 'stroke-linecap': 'round', 'stroke-dasharray': c, 'stroke-dashoffset': c });
  requestAnimationFrame(() => requestAnimationFrame(() => bar.setAttribute('stroke-dashoffset', c * (1 - p))));
  const el = h('div', { class: 'ring', role: 'img', 'aria-label': `Выполнено ${done} из ${total}`, style: { width: size + 'px', height: size + 'px' } },
    h('svg', { width: size, height: size, viewBox: `0 0 ${size} ${size}`, 'aria-hidden': 'true' },
      h('circle', { class: 'trk', cx: size / 2, cy: size / 2, r, fill: 'none', 'stroke-width': stroke }), bar),
    h('b', { style: { fontSize: size >= 90 ? Math.round(size * 0.3) + 'px' : null } }, text ?? `${done}/${total}`));
  el.update = (d) => bar.setAttribute('stroke-dashoffset', c * (1 - Math.min(1, d / total)));
  return el;
}

export function skeleton(kind = 'card', n = 2) {
  const one = () => kind === 'line'
    ? h('div', { class: 'skel', style: { height: '16px', width: '70%' } })
    : h('div', { class: 'card tight stack', 'aria-hidden': 'true' },
      h('div', { class: 'skel', style: { height: '14px', width: '38%' } }),
      h('div', { class: 'skel', style: { height: '20px', width: '86%' } }),
      h('div', { class: 'skel', style: { height: '14px', width: '60%' } }));
  return h('div', { class: 'stack', 'aria-busy': 'true', 'aria-label': 'Загружаю' }, Array.from({ length: n }, one));
}

export function errorState(err, retry) {
  const offline = err?.status === 0;
  return h('div', { class: 'state', role: 'alert' },
    h('div', { class: 'ico' }, icon(offline ? 'globe' : 'alert', 26)),
    h('h2', { class: 'h2' }, offline ? 'Нет связи' : 'Не получилось загрузить'),
    h('p', null, err?.friendly || 'Попробуй ещё раз.'),
    retry ? h('button', { class: 'btn soft', type: 'button', onclick: retry }, icon('refresh', 18), 'Повторить') : null);
}

export function emptyState(ic, title, text, action) {
  return h('div', { class: 'state' },
    h('div', { class: 'ico' }, icon(ic, 26)),
    h('h2', { class: 'h2' }, title),
    text ? h('p', null, text) : null,
    action || null);
}

// ---------- материалы ----------
export function accessLabel(m) {
  if (m.access === 'free' || !m.is_paid && m.access !== 'subscription') return ['бесплатно', 'tag-free'];
  if (m.access === 'subscription') return ['Подписка Курс+', 'tag-paid'];
  return [m.kind === 'course' ? 'платный курс' : 'платный материал', 'tag-paid'];
}

export function openMaterial(m, { paid = false } = {}) {
  api.click(m.id);
  haptic('light');
  tg.openLink(paid ? (m.purchase_url || m.url) : m.url);
}

// Платные материалы: только польза и ссылка, содержание не показываем.
export function materialCard(m, why, { actions } = {}) {
  const [lbl, cls] = accessLabel(m);
  const paid = m.is_paid || m.access === 'paid' || m.access === 'subscription';
  const meta = h('div', { class: 'meta' },
    h('span', null, m.kind_label || m.kind), h('span', { 'aria-hidden': 'true' }, '·'), h('span', { class: cls }, lbl),
    m.duration ? [h('span', { 'aria-hidden': 'true' }, '·'), h('span', null, m.duration)] : null,
    paid && m.access !== 'subscription' && m.price ? [h('span', { 'aria-hidden': 'true' }, '·'), h('span', null, m.price)] : null);
  const head = paid
    ? h('div', { class: 'ti' }, m.title)
    : h('a', { class: 'ti', href: m.url, target: '_blank', rel: 'noopener', onclick: (e) => { e.preventDefault(); openMaterial(m); } }, m.title);
  return h('div', { class: 'mat' },
    h('div', { class: 'k', 'aria-hidden': 'true' }, m.icon || '📄'),
    h('div', { class: 'grow' }, head, meta,
      why ? h('p', { class: 'why' }, why) : null,
      paid ? h('div', { class: 'cta' }, h('button', { class: 'btn soft sm', type: 'button', onclick: () => openMaterial(m, { paid: true }) }, icon('gem', 16), 'Подробнее о курсе')) : null,
      actions || null));
}
