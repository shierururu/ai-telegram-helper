// Создание и управление напоминаниями (листы). Используется из чата, маршрута, налогов и раздела «Напоминания».
import { h, icon, openSheet, toast, confirmSheet, haptic } from './ui.js';
import { api, ApiError } from './api.js';
import { tg } from './tg.js';
import { state, refreshReminders } from './state.js';
import { FREQ, FREQ_SHORT, uuid, whenText, ymd, pad } from './fmt.js';
import { chipsPick } from './components.js';

const EXAMPLES = ['завтра в 15:30 отправить счёт', 'каждый понедельник в 9 планёрка', 'каждый день в 20:00 разобрать почту'];

export function reminderLimitReached() { return (state.boot?.reminders?.length || 0) >= 5; }

// prefill: { event, link, frequency, text }
export function openReminderComposer(prefill = {}) {
  return new Promise((resolve) => {
    let result = null;
    const opId = uuid();
    const draft = { event: prefill.event || '', local_at: null, timezone: state.boot?.settings?.timezone || null, frequency: prefill.frequency || 'once', link: prefill.link || null };
    let offMain = () => {};
    const sheet = openSheet({
      title: 'Новое напоминание',
      onClose: () => { offMain(); resolve(result); },
      build: (close, api2) => {
        const host = h('div', { class: 'stack-lg' });
        const go = (step) => { offMain(); offMain = () => {}; host.replaceChildren(step()); };

        const stepInput = () => {
          const ta = h('textarea', { class: 'input', placeholder: 'Например: завтра в 15:30 отправить счёт', 'aria-label': 'Что и когда напомнить', rows: 3 });
          const err = h('p', { class: 'err', role: 'alert', hidden: true });
          const btn = h('button', { class: 'btn primary block', type: 'button' }, 'Продолжить');
          const submit = async () => {
            const text = ta.value.trim(); if (!text) { ta.focus(); return; }
            btn.disabled = true; btn.replaceChildren(h('span', { class: 'spin' })); err.hidden = true;
            try { applyParsed(await api.parseReminder(text, draft.timezone || undefined)); draft._text = text; next(); }
            catch (e) { err.textContent = e.friendly; err.hidden = false; btn.disabled = false; btn.replaceChildren('Продолжить'); }
          };
          btn.onclick = submit;
          return h('div', { class: 'stack' },
            h('p', { class: 'muted' }, 'Напиши своими словами, о чём и когда напомнить. Если нужно, повторю регулярно.'),
            ta,
            h('div', { class: 'chips' }, EXAMPLES.map((x) => h('button', { class: 'chip', type: 'button', onclick: () => { ta.value = x; ta.focus(); } }, x))),
            err, btn);
        };
        const applyParsed = (p) => {
          draft.event = p.event || draft.event; draft.local_at = p.local_at || null; draft.frequency = p.frequency || 'once';
          draft.timezone = p.timezone || draft.timezone; draft.link = p.link || draft.link; draft._needs = p.needs || [];
        };
        const next = () => go(draft._needs?.includes('timezone') ? stepTz : stepReview);

        const stepTz = () => {
          const inp = h('input', { class: 'input', placeholder: 'Например: Омск', 'aria-label': 'Город', autocomplete: 'address-level2' });
          const err = h('p', { class: 'err', role: 'alert', hidden: true });
          const btn = h('button', { class: 'btn primary block', type: 'button' }, 'Продолжить');
          const submit = async () => {
            if (!inp.value.trim()) { inp.focus(); return; }
            btn.disabled = true; btn.replaceChildren(h('span', { class: 'spin' })); err.hidden = true;
            try {
              const r = await api.resolveTz(inp.value.trim());
              draft.timezone = r.timezone; if (state.boot) state.boot.settings.timezone = r.timezone;
              if (draft._text) applyParsed(await api.parseReminder(draft._text, r.timezone)); else draft._needs = ['time'];
              next();
            } catch (e) { err.textContent = e.friendly; err.hidden = false; btn.disabled = false; btn.replaceChildren('Продолжить'); inp.classList.add('bad'); }
          };
          btn.onclick = submit; inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
          return h('div', { class: 'stack' },
            h('p', { class: 'muted' }, 'В каком ты городе? Нужно, чтобы напоминание пришло в нужный час.'),
            h('div', { class: 'field' }, h('label', null, 'Город'), inp), err, btn);
        };

        const stepReview = () => {
          const ev = h('input', { class: 'input', value: draft.event, 'aria-label': 'Что напомнить', placeholder: 'О чём напомнить' });
          const at = draft.local_at ? new Date(draft.local_at) : null;
          const date = h('input', { class: 'input', type: 'date', min: ymd(new Date()), value: at ? ymd(at) : '', 'aria-label': 'Дата' });
          const time = h('input', { class: 'input', type: 'time', value: at ? `${pad(at.getHours())}:${pad(at.getMinutes())}` : '', 'aria-label': 'Время' });
          const err = h('p', { class: 'err', role: 'alert', hidden: true });
          const btn = h('button', { class: 'btn primary block', type: 'button' }, 'Создать напоминание');
          if (tg.inTelegram) btn.hidden = true;
          const valid = () => ev.value.trim() && date.value && time.value && draft.timezone;
          const sync = () => { btn.disabled = !valid(); if (tg.inTelegram) { offMain(); offMain = tg.showMain('Создать напоминание', submit, { disabled: !valid() }); } };
          const submit = async () => {
            if (!valid()) return;
            btn.disabled = true; btn.replaceChildren(h('span', { class: 'spin' })); err.hidden = true;
            if (tg.inTelegram) { offMain(); offMain = tg.showMain('Создаю…', () => {}, { loading: true }); }
            try {
              const r = await api.createReminder({ event: ev.value.trim(), local_at: `${date.value}T${time.value}:00`, timezone: draft.timezone, frequency: draft.frequency, link: draft.link, operation_id: opId });
              result = r; haptic('success');
              await refreshReminders().catch(() => {});
              toast(`Готово. Напомню ${r.timing}`);
              close();
            } catch (e) {
              haptic('error'); err.textContent = e.status === 409 && e.code === 'limit' ? e.friendly + ' Открой «Напоминания» и отключи ненужное.' : e.friendly;
              err.hidden = false; btn.replaceChildren('Создать напоминание'); sync();
            }
          };
          btn.onclick = submit;
          for (const el of [ev, date, time]) el.addEventListener('input', sync);
          const needsHint = draft._needs?.includes('time') || !draft.local_at;
          setTimeout(sync, 0);
          return h('div', { class: 'stack' },
            needsHint ? h('p', { class: 'hint' }, 'Выбери дату и время — я не стал угадывать.') : null,
            h('div', { class: 'field' }, h('label', null, 'Что напомнить'), ev),
            h('div', { class: 'row' }, h('div', { class: 'field grow' }, h('label', null, 'Дата'), date), h('div', { class: 'field grow' }, h('label', null, 'Время'), time)),
            h('div', { class: 'field' }, h('span', { class: 'label' }, 'Повторять'),
              chipsPick(Object.entries(FREQ), draft.frequency, (v) => { draft.frequency = v || 'once'; })),
            draft.link ? h('div', { class: 'row small muted' }, icon('ext', 16), h('span', { class: 'grow' }, 'Со ссылкой: ' + (draft.link.title || draft.link.url))) : null,
            draft.timezone ? h('p', { class: 'hint' }, 'Часовой пояс: ' + draft.timezone) : h('p', { class: 'err' }, 'Не знаю твой часовой пояс — вернись и укажи город.'),
            err, btn);
        };

        if (prefill.text) { go(stepInput); host.querySelector('textarea').value = prefill.text; }
        else if (prefill.event) { draft._needs = ['time']; go(draft.timezone ? stepReview : stepTz); if (!draft.timezone) draft._text = prefill.event; }
        else go(stepInput);
        return host;
      },
    });
    void sheet;
  });
}

// Управление существующим напоминанием. Возвращает Promise<boolean> — менялось ли что-то.
export function openReminderManage(r) {
  return new Promise((resolve) => {
    let changed = false;
    const cur = { ...r };
    openSheet({
      title: 'Напоминание',
      onClose: () => resolve(changed),
      build: (close, api2) => {
        const timing = h('p', { class: 'muted' });
        const draw = () => { timing.textContent = `${cur.timing}${cur.next_due ? ' · ближайшее: ' + whenText(cur.next_due) : ''}`; };
        draw();
        const conflict = async (e) => {
          if (e instanceof ApiError && e.status === 409) { toast(e.message || 'Напоминание изменилось. Обновил список.'); changed = true; close(); return; }
          toast(e.friendly || 'Не получилось. Попробуй ещё раз.'); haptic('error');
        };
        const update = (p) => { Object.assign(cur, p); changed = true; draw(); };
        return h('div', { class: 'stack-lg' },
          h('div', { class: 'stack' }, h('h3', { class: 'h2' }, cur.event), timing,
            cur.link ? h('a', { class: 'btn soft sm', href: cur.link.url, onclick: (e) => { e.preventDefault(); tg.openLink(cur.link.url); } }, icon('ext', 16), cur.link.title || 'Открыть ссылку') : null),
          h('div', { class: 'field' }, h('span', { class: 'label' }, 'Как часто'),
            h('div', { class: 'chips' }, Object.entries(FREQ).map(([k, v]) => h('button', {
              class: 'chip' + (cur.frequency === k ? ' on' : ''), type: 'button', 'aria-pressed': String(cur.frequency === k),
              onclick: async (e) => {
                if (k === cur.frequency) return;
                try { const n = await api.patchReminder(cur.id, { version: cur.version, frequency: k }); update(n); haptic('success'); toast(`Теперь: ${n.timing}`);
                  e.target.parentNode.querySelectorAll('.chip').forEach((c) => { const on = c.textContent === v; c.classList.toggle('on', on); c.setAttribute('aria-pressed', String(on)); });
                } catch (er) { conflict(er); }
              } }, v)))),
          snoozeRow(cur, update, conflict),
          h('button', { class: 'btn text danger', type: 'button', onclick: async () => {
            const ok = await confirmSheet({ title: 'Отключить напоминание?', text: `«${cur.event}» больше не будет приходить.`, confirm: 'Отключить', danger: true });
            if (!ok) return;
            try { await api.deleteReminder(cur.id, cur.version); changed = true; toast('Напоминание отключено'); close(); } catch (er) { conflict(er); }
          } }, icon('trash', 18), 'Отключить напоминание'));
      },
    });
  });
}

function snoozeRow(cur, update, conflict) {
  const mk = (kind, text) => h('button', { class: 'btn soft grow', type: 'button', onclick: async () => {
    try { const n = await api.snooze(cur.id, cur.version, kind); update(n); haptic('success'); toast(`Отложено. ${n.timing}`); } catch (e) { conflict(e); }
  } }, icon('clock', 18), text);
  return h('div', { class: 'field' }, h('span', { class: 'label' }, 'Отложить ближайшее'), h('div', { class: 'actions' }, mk('h1', '+1 час'), mk('d1', 'До завтра')));
}

export { FREQ_SHORT };
