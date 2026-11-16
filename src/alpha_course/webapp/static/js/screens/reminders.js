// «Напоминания»: список, управление, создание из свободного текста.
import { h, icon } from '../ui.js';
import { state, refreshReminders } from '../state.js';
import { skeleton, errorState, emptyState } from '../components.js';
import { FREQ_SHORT, whenText } from '../fmt.js';
import { openReminderComposer, openReminderManage } from '../reminder-flow.js';

export default async function mount(view, { query }) {
  const host = h('div', { class: 'stack-lg' });
  view.replaceChildren(host);

  const create = async () => { const r = await openReminderComposer(query.get('text') ? { text: query.get('text') } : {}); if (r) draw(); };

  function draw() {
    const list = state.boot.reminders || [];
    const add = h('button', { class: 'btn primary', type: 'button', onclick: create }, icon('plus', 18), 'Новое напоминание');
    if (!list.length) { host.replaceChildren(emptyState('bell', 'Пока ничего не запланировано', 'Напиши, о чём напомнить — например, «завтра в 15:30 отправить счёт» или «каждый понедельник в 9 планёрка».', add)); return; }
    host.replaceChildren(
      h('div', { class: 'row', style: { justifyContent: 'space-between' } }, h('p', { class: 'muted small' }, `Активных: ${list.length} из 5`), add),
      h('div', { class: 'list', role: 'list' }, list.map((r) => h('button', { class: 'item', type: 'button', role: 'listitem', 'aria-label': `${r.event}, ${r.timing}. Управлять`,
        onclick: async () => { if (await openReminderManage(r)) { await refreshReminders().catch(() => {}); draw(); } } },
        h('div', { class: 'ico' }, icon(r.frequency === 'once' ? 'bell' : 'repeat', 20)),
        h('div', { class: 'grow' }, h('div', { class: 't' }, r.event), h('div', { class: 's' }, r.timing),
          h('div', { class: 'chips', style: { marginTop: '6px' } }, h('span', { class: 'chip static' }, FREQ_SHORT[r.frequency] || r.frequency), r.link ? h('span', { class: 'chip static accent' }, 'со ссылкой') : null)),
        h('span', { class: 'chev' }, icon('chev', 20))))),
      h('p', { class: 'hint' }, 'Нажми на напоминание, чтобы изменить частоту, отложить или отключить его.'));
  }

  host.replaceChildren(skeleton('card', 2));
  try { await refreshReminders(); draw(); if (query.get('new')) create(); }
  catch (e) { host.replaceChildren(errorState(e, () => mount(view, { query }))); }
}
