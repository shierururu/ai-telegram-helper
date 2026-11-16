// «Налоги»: короткая форма профиля и календарь сроков.
import { h, icon, haptic, toast, busyButton } from '../ui.js';
import { api } from '../api.js';
import { state, fields, options, setProfile } from '../state.js';
import { pageHead, segmented, chipsPick, skeleton, errorState, section } from '../components.js';
import { parseYmd, ymd, daysUntil, inDays, dateLong, monthShort, weekdayShort, startOfDay } from '../fmt.js';

let range = 'month';

export default async function mount(view, { go }) {
  const host = h('div', { class: 'stack-lg' });
  view.replaceChildren(host);

  async function load() {
    host.replaceChildren(skeleton('card', 3));
    try {
      const t = await api.taxes();
      state.boot.taxes_ready = !!t.ready;
      t.ready ? calendar(t) : form(t.missing);
    } catch (e) { host.replaceChildren(errorState(e, load)); }
  }

  function form(missing, editing = false) {
    const f = fields();
    const sel = { legal_form: f.legal_form ?? null, tax_regime: f.tax_regime ?? null, employees: f.employees ?? null };
    const btn = h('button', { class: 'btn primary block', type: 'button' }, 'Показать сроки');
    const err = h('p', { class: 'err', role: 'alert', hidden: true });
    const chk = () => { btn.disabled = sel.legal_form == null || sel.tax_regime == null || sel.employees == null; };
    busyButton(btn, async () => {
      try { setProfile(await api.saveProfile(sel)); haptic('success'); await load(); } catch (e) { err.textContent = e.friendly; err.hidden = false; }
    });
    chk();
    host.replaceChildren(
      h('div', { class: 'card stack' },
        h('p', { class: 'muted' }, editing ? 'Поменяй данные — календарь пересоберётся.' : 'Чтобы показать только твои сроки, нужны три коротких ответа. Даты не выдумываю — считаю по профилю.'),
        h('div', { class: 'field' }, h('span', { class: 'label' }, 'Форма бизнеса'), chipsPick(options('legal_forms'), sel.legal_form, (v) => { sel.legal_form = v; chk(); })),
        h('div', { class: 'field' }, h('span', { class: 'label' }, 'Налоговый режим'), chipsPick(options('tax_regimes'), sel.tax_regime, (v) => { sel.tax_regime = v; chk(); })),
        h('div', { class: 'field' }, h('span', { class: 'label' }, 'Есть сотрудники?'), chipsPick([[true, 'Да'], [false, 'Нет']], sel.employees, (v) => { sel.employees = v; chk(); })),
        err, btn));
  }

  function calendar(t) {
    const today = startOfDay(new Date());
    const items = t.items.map((x) => ({ ...x, d: parseYmd(x.date) })).sort((a, b) => a.d - b.d);
    let sel = null; // выбранный день
    const f = fields();
    const labels = [f.legal_form, f.tax_regime, f.employees === true ? 'есть сотрудники' : f.employees === false ? 'без сотрудников' : null].filter(Boolean);

    const strip = h('div', { class: 'days', role: 'group', 'aria-label': 'Дни' });
    const list = h('div', { class: 'stack', 'aria-live': 'polite' });
    const seg = segmented([['week', 'Неделя'], ['month', 'Месяц'], ['all', 'Все']], range, (v) => { range = v; sel = null; draw(); }, 'Период');

    function draw() {
      const span = range === 'week' ? 7 : 30;
      const days = Array.from({ length: span }, (_, i) => new Date(today.getFullYear(), today.getMonth(), today.getDate() + i));
      const has = new Set(items.map((x) => x.date));
      strip.hidden = range === 'all';
      strip.replaceChildren(...days.map((d, i) => {
        const key = ymd(d);
        return h('button', { class: 'day-c' + (has.has(key) ? ' has' : '') + (i === 0 ? ' today' : '') + (sel === key ? ' sel' : ''), type: 'button', 'aria-pressed': String(sel === key),
          'aria-label': `${dateLong(d)}${has.has(key) ? ', есть срок' : ''}`,
          onclick: () => { sel = sel === key ? null : key; haptic('select'); draw(); } },
          h('small', null, weekdayShort(d)), h('b', null, d.getDate()), h('i'));
      }));
      const shown = items.filter((x) => (sel ? x.date === sel : range === 'all' ? true : daysUntil(x.d) < span));
      list.replaceChildren(...(shown.length ? shown.map(item) : [h('div', { class: 'card tight muted small' },
        sel ? 'В этот день сроков нет.' : `В ближайшие ${span === 7 ? '7' : '30'} дней сроков нет.`,
        range !== 'all' ? [' ', h('button', { class: 'link', type: 'button', onclick: () => { range = 'all'; sel = null; seg.set('all'); draw(); } }, 'Показать все')] : null)]));
    }

    function item(x) {
      const n = daysUntil(x.d);
      return h('article', { class: 'card tight row', style: { alignItems: 'flex-start' } },
        h('div', { class: 'item', style: { padding: 0, minHeight: 0, alignItems: 'flex-start', flex: 1 } },
          h('div', { class: 'ico', 'aria-hidden': 'true' }, icon(x.kind === 'payment' ? 'wallet' : 'doc', 20)),
          h('div', { class: 'grow stack', style: { gap: '4px' } },
            h('div', { class: 't' }, x.title),
            h('div', { class: 's' }, `${dateLong(x.d)} · ${inDays(n)}`),
            x.note ? h('div', { class: 's' }, x.note) : null,
            h('div', { class: 'chips', style: { marginTop: '2px' } },
              h('span', { class: 'chip static ' + (x.kind === 'payment' ? 'accent' : '') }, x.kind === 'payment' ? 'Оплата' : 'Отчёт'),
              x.shifted ? h('span', { class: 'chip static warn' }, 'Сдвинут: выпадает на выходной') : null,
              n <= 3 && n >= 0 ? h('span', { class: 'chip static warn' }, 'Скоро') : null))));
    }

    const remind = h('button', { class: 'btn primary block', type: 'button' }, icon('bell', 18), 'Напоминать за 3 дня');
    busyButton(remind, async () => {
      try { const r = await api.taxesRemind(); haptic('success'); toast(r.created ? `Готово: создано напоминаний — ${r.created}` : 'Напоминания по этим срокам уже созданы'); }
      catch (e) { haptic('error'); toast(e.friendly); }
    });

    host.replaceChildren(
      h('div', { class: 'row', style: { justifyContent: 'space-between', alignItems: 'center' } },
        h('div', { class: 'chips grow' }, labels.map((l) => h('span', { class: 'chip static' }, l))),
        h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Изменить данные', title: 'Изменить', onclick: () => form([], true) }, icon('edit', 20))),
      seg, strip, list, remind,
      h('div', { class: 'card flat tight row', style: { alignItems: 'flex-start' } }, icon('info', 20), h('p', { class: 'small muted grow' }, 'Показываю только сроки, которые можно рассчитать по твоему профилю. Если не уверен в дате — не придумываю её. Перед оплатой сверься с официальным календарём налоговой.')));
    draw();
  }

  load();
}
