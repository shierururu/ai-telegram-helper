// «Настройки»: город, итоги недели и управление уведомлениями.
import { h, icon, haptic, toast } from '../ui.js';
import { api } from '../api.js';
import { state } from '../state.js';
import { segmented, switchBtn, skeleton, errorState } from '../components.js';

export default async function mount(view) {
  const host = h('div', { class: 'stack-lg' });
  view.replaceChildren(host);
  host.replaceChildren(skeleton('card', 2));
  let s;
  try { s = await api.settings(); } catch (e) { host.replaceChildren(errorState(e, () => mount(view))); return; }
  state.boot.settings = s;

  const save = async (patch) => {
    try { s = await api.saveSettings(patch); state.boot.settings = s; return s; }
    catch (e) { haptic('error'); toast(e.friendly); throw e; }
  };

  function draw() {
    const quietRow = h('div', { class: 'row', style: { padding: '0 var(--s-4) var(--s-4)' } },
      timeField('С', s.quiet_start, (v) => save({ quiet_start: v })), timeField('До', s.quiet_end, (v) => save({ quiet_end: v })));
    quietRow.hidden = !s.quiet_enabled;
    const city = h('input', { class: 'input', value: s.timezone ? '' : '', placeholder: 'Например: Омск', 'aria-label': 'Город', autocomplete: 'address-level2' });
    const err = h('p', { class: 'err', role: 'alert', hidden: true });
    const saveCity = h('button', { class: 'btn soft', type: 'button', onclick: async () => {
      if (!city.value.trim()) return city.focus();
      try { await save({ city: city.value.trim() }); city.value = ''; err.hidden = true; haptic('success'); toast('Город обновлён'); draw(); } catch (e) { err.textContent = e.friendly; err.hidden = false; city.classList.add('bad'); }
    } }, 'Сохранить');
    city.addEventListener('input', () => city.classList.remove('bad'));

    host.replaceChildren(
      h('div', { class: 'list' },
        h('div', { class: 'item' }, h('div', { class: 'grow' }, h('div', { class: 't' }, 'Итоги недели'), h('div', { class: 's' }, `Воскресенье, 19:00 (${s.timezone || 'по Москве, Europe/Moscow'}). Активность в боте и отметки об обучении.`)),
          switchBtn(!!s.weekly_summary, async (v) => { await save({ weekly_summary: v, weekly_summary_asked: true }); draw(); }, 'Итоги недели')),
        h('div', { class: 'item' }, h('div', { class: 'ico' }, icon('bell', 20)), h('div', { class: 'grow' }, h('div', { class: 't' }, 'Уведомления'), h('div', { class: 's' }, s.enabled ? 'Напоминания приходят' : 'Всё выключено — ничего не пришлю')),
          switchBtn(s.enabled, async (v) => { await save({ enabled: v }); s.enabled = v; draw(); }, 'Все уведомления')),
        h('div', null, h('div', { class: 'item' }, h('div', { class: 'ico' }, icon('moon', 20)), h('div', { class: 'grow' }, h('div', { class: 't' }, 'Тихие часы'), h('div', { class: 's' }, 'В это время не буду беспокоить')),
          switchBtn(s.quiet_enabled, async (v) => { await save({ quiet_enabled: v }); s.quiet_enabled = v; quietRow.hidden = !v; }, 'Тихие часы')), quietRow)),
      h('div', { class: 'card stack' }, h('div', null, h('h2', { class: 'h2' }, 'Не больше в неделю'), h('p', { class: 'small muted' }, 'Предел сообщений по плану и напоминаниям.')),
        segmented([[3, '3'], [7, '7'], [14, '14']], s.weekly_limit, async (v) => { try { await save({ weekly_limit: v }); haptic('success'); } catch (e) { draw(); } }, 'Лимит в неделю')),
      h('div', { class: 'card stack' }, h('div', null, h('h2', { class: 'h2' }, 'Город'), h('p', { class: 'small muted' }, s.timezone ? `Часовой пояс: ${s.timezone}` : 'Не указан — напоминания пока не отправить.')),
        h('div', { class: 'row' }, h('div', { class: 'grow' }, city), saveCity), err));
    if (!s.enabled) host.querySelectorAll('.card').forEach((c) => { c.style.opacity = '.6'; });
  }

  function timeField(label, val, onSave) {
    const inp = h('input', { class: 'input', type: 'time', value: val, 'aria-label': label === 'С' ? 'Тихие часы с' : 'Тихие часы до' });
    inp.addEventListener('change', () => { if (inp.value) onSave(inp.value).then?.(() => {}, () => {}); });
    return h('div', { class: 'field grow' }, h('label', null, label), inp);
  }
  draw();
}
