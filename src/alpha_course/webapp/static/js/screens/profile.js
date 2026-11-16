// «Профиль»: поля бизнеса, описание своими словами, удаление.
import { h, icon, haptic, toast, openSheet, confirmSheet, busyButton } from '../ui.js';
import { api } from '../api.js';
import { state, FIELDS, LEVELS, options, showValue, fields, setProfile, completeness } from '../state.js';
import { ring, chipsPick } from '../components.js';

export default function mount(view) {
  const host = h('div', { class: 'stack-lg' });
  view.replaceChildren(host);

  function draw() {
    const f = fields(); const { n, total } = completeness();
    const rg = ring(n, total, 60, 6);
    host.replaceChildren(
      h('div', { class: 'card row' }, rg, h('div', { class: 'grow' }, h('h2', { class: 'h2' }, n === total ? 'Профиль заполнен' : `Заполнено ${n} из ${total}`), h('p', { class: 'small muted' }, 'Чем точнее профиль, тем полезнее советы. Но он не обязателен.'))),
      h('div', { class: 'list' }, FIELDS.map((fd) => {
        const v = showValue(fd.key, f[fd.key]);
        return h('button', { class: 'item', type: 'button', onclick: () => edit(fd), 'aria-label': `${fd.label}: ${v || 'не указано'}. Изменить` },
          h('div', { class: 'grow' }, h('div', { class: 't' }, fd.label)), h('span', { class: 'val' + (v ? '' : ' empty') }, v || 'Не указано'), h('span', { class: 'chev' }, icon('chev', 20)));
      })),
      h('div', { class: 'list' }, h('button', { class: 'item', type: 'button', onclick: describe },
        h('div', { class: 'ico' }, icon('spark', 20)),
        h('div', { class: 'grow' }, h('div', { class: 't' }, 'Описать бизнес своими словами'), h('div', { class: 's' }, 'Напиши пару фраз — заполню профиль сам')), h('span', { class: 'chev' }, icon('chev', 20)))),
      h('button', { class: 'btn text danger block', type: 'button', onclick: wipe }, icon('trash', 18), 'Удалить данные профиля'));
  }

  function edit(fd) {
    const cur = fields()[fd.key] ?? null;
    const opts = fd.bool ? [[true, 'Есть'], [false, 'Нет']] : fd.levels ? Object.entries(LEVELS) : options(fd.opts).map((x) => [x, x]);
    openSheet({ title: fd.label, build: (close) => h('div', { class: 'stack' },
      chipsPick(opts, cur, async (v) => {
        if (v === null) return;
        try { setProfile(await api.saveProfile({ [fd.key]: v })); haptic('success'); toast('Сохранил'); close(); draw(); } catch (e) { haptic('error'); toast(e.friendly); }
      }, { required: true }),
      h('p', { class: 'hint' }, 'Выбор сохранится сразу.')) });
  }

  function describe() {
    openSheet({ title: 'Расскажи о бизнесе', build: (close) => {
      const ta = h('textarea', { class: 'input', rows: 4, placeholder: 'Например: у меня небольшая кофейня, работаю как ИП, открылась три месяца назад, сотрудников нет', 'aria-label': 'Описание бизнеса' });
      const out = h('div', { 'aria-live': 'polite' });
      const btn = h('button', { class: 'btn primary block', type: 'button' }, 'Запомнить');
      busyButton(btn, async () => {
        if (!ta.value.trim()) return ta.focus();
        try {
          const r = await api.describe(ta.value.trim()); setProfile(r.profile); const up = Object.entries(r.updates || {});
          if (!up.length) { out.replaceChildren(h('p', { class: 'hint' }, 'Ничего нового не нашёл. Попробуй назвать сферу, форму бизнеса или цель.')); return; }
          haptic('success'); draw();
          out.replaceChildren(h('div', { class: 'personal', style: { background: 'var(--ok-soft)', borderRadius: '14px', padding: '12px 16px', fontSize: 'var(--t-sm)' } },
            h('b', null, 'Запомнил: '), up.map(([k, v]) => `${(FIELDS.find((x) => x.key === k)?.label || k).toLowerCase()} — ${showValue(k, v) ?? v}`).join('; ')));
          btn.replaceWith(h('button', { class: 'btn soft block', type: 'button', onclick: () => close() }, 'Готово'));
        } catch (e) { out.replaceChildren(h('p', { class: 'err' }, e.friendly)); }
      });
      return h('div', { class: 'stack' }, h('p', { class: 'muted' }, 'Я сам выделю главное. Личные данные (имена, телефоны) писать не нужно.'), ta, out, btn);
    } });
  }

  async function wipe() {
    if (!await confirmSheet({ title: 'Удалить профиль?', text: 'Сфера, цель, налоговый режим и другие ответы будут стёрты. Советы станут менее точными.', confirm: 'Удалить', danger: true })) return;
    try { setProfile(await api.clearProfile()); state.boot.taxes_ready = false; toast('Профиль очищен'); draw(); } catch (e) { toast(e.friendly); }
  }

  draw();
}
