// Единственный учебный маршрут: добровольный план на 7 дней.
import { h, icon, haptic, toast, confirmSheet, busyButton } from '../ui.js';
import { api } from '../api.js';
import { tg } from '../tg.js';
import { state, fields, options, setProfile } from '../state.js';
import { pageHead, ring, skeleton, errorState, openMaterial, chipsPick } from '../components.js';
import { whenText } from '../fmt.js';

let presetGoal = null;

export default function mount(view, { query }) {
  presetGoal = query.get('goal') ? query.get('goal').slice(0, 120) : null;
  const body = h('div', { class: 'stack-lg' });
  view.replaceChildren(pageHead('Учёба по шагам', 'План на 7 дней'), body);
  let offMain = () => {};
  week(body, (fn) => { offMain(); offMain = fn; });
  return () => offMain();
}

/* ---------------- 7 дней ---------------- */
async function week(host, setOff) {
  host.replaceChildren(skeleton('card', 2));
  try { state.boot.journey = await api.journey(); } catch (e) { if (!state.boot.journey) { host.replaceChildren(errorState(e, () => week(host, setOff))); return; } }
  const j = state.boot.journey;
  if (j && j.status === 'active') activeView(host, j, setOff); else startFlow(host, setOff);
}

function activeView(host, j, setOff) {
  setOff(() => {});
  const doneN = j.done_days, total = j.steps.length;
  const rg = ring(doneN, total, 60, 6);
  const head = h('div', { class: 'card row' }, rg, h('div', { class: 'grow' }, h('div', { class: 'eyebrow' }, 'Цель'), h('h2', { class: 'h2' }, j.goal[0].toUpperCase() + j.goal.slice(1)), h('p', { class: 'small muted' }, `Пройдено ${doneN} из ${total} · шаг в день в ${j.time}`)));

  const markDone = async (s, btn) => {
    try { state.boot.journey = await api.journeyDone(s.day); haptic('success'); toast(state.boot.journey.status === 'done' ? 'План выполнен. Отличная работа!' : 'Шаг выполнен'); week(host, setOff); }
    catch (e) { haptic('error'); toast(e.friendly); }
  };
  const list = h('ol', { class: 'stack' }, j.steps.map((s) => {
    const open = () => openMaterial({ id: s.material_id, url: s.url });
    if (s.state === 'done') return h('li', { class: 'step is-done' }, h('span', { class: 'mark done' }, icon('check', 16)), h('div', { class: 'grow' }, h('div', { class: 'day' }, `День ${s.day}`), h('div', { class: 'task' }, s.task)));
    if (s.state === 'locked') return h('li', { class: 'step is-locked' }, h('span', { class: 'mark lock' }, icon('lock', 14)), h('div', { class: 'grow' }, h('div', { class: 'day' }, `День ${s.day}` + (s.due ? ` · ${whenText(s.due)}` : '')), h('div', { class: 'task' }, s.task)));
    const doneBtn = h('button', { class: s.state === 'today' ? 'btn on-dark' : 'btn soft sm', type: 'button' }, icon('check', 18), 'Готово');
    busyButton(doneBtn, () => markDone(s));
    const openBtn = h('button', { class: s.state === 'today' ? 'btn primary grow' : 'btn line sm grow', type: 'button', onclick: open }, 'Открыть материал');
    if (s.state === 'today') return h('li', { class: 'step is-today' }, h('div', { class: 'row', style: { alignItems: 'flex-start' } }, h('span', { class: 'mark now' }), h('div', { class: 'grow' }, h('div', { class: 'day' }, `День ${s.day} · сегодня`), h('div', { class: 'task' }, s.task), h('div', { class: 'small muted', style: { marginTop: '4px' } }, `Материал: «${s.title}»`))), h('div', { class: 'actions' }, openBtn, doneBtn));
    return h('li', { class: 'step is-missed' }, h('span', { class: 'mark warn' }, icon('clock', 14)), h('div', { class: 'grow stack' }, h('div', null, h('div', { class: 'day', style: { color: 'var(--warn)' } }, `День ${s.day} · пропущен`), h('div', { class: 'task' }, s.task)), h('div', { class: 'actions' }, openBtn, doneBtn)));
  }));
  const stop = h('button', { class: 'btn text', type: 'button', onclick: async () => {
    const ok = await confirmSheet({ title: 'Остановить план?', text: 'Сообщения по плану перестанут приходить. Новый план можно составить в любой момент.', confirm: 'Остановить', danger: true });
    if (!ok) return;
    try { await api.journeyStop(); state.boot.journey = null; toast('План остановлен'); week(host, setOff); } catch (e) { toast(e.friendly); }
  } }, 'Остановить план');
  host.replaceChildren(head, list, h('div', { style: { textAlign: 'center' } }, stop));
}

function startFlow(host, setOff) {
  const st = { goal: presetGoal || fields().goal || null, time: '09:00', city: '', preview: null };
  const go = (fn) => { setOff(() => {}); host.replaceChildren(fn()); };
  const back = (fn, label = 'Назад') => h('button', { class: 'btn text sm', type: 'button', onclick: () => go(fn) }, icon('back', 16), label);

  const intro = () => h('div', { class: 'stack-lg' },
    h('div', { class: 'card stack' },
      h('h2', { class: 'h2' }, 'План на 7 дней'),
      h('p', { class: 'muted' }, 'Каждый день один маленький шаг по материалам из базы знаний — к твоей цели. В выбранное время пришлю напоминание.'),
      h('ul', { class: 'stack small' }, ['Один шаг в день, 10–20 минут', 'Сначала покажу весь план — запустишь, только если захочешь', 'Остановить можно в любой момент'].map((t) => h('li', { class: 'row' }, h('span', { class: 'mark done', style: { width: '20px', height: '20px', animation: 'none' } }, icon('check', 12)), h('span', null, t)))),
      h('button', { class: 'btn primary block', type: 'button', onclick: () => go(next0) }, 'Составить план')));

  const next0 = () => (!st.goal ? goalStep() : !state.boot.settings?.timezone ? cityStep() : timeStep());

  const goalStep = () => {
    const err = h('p', { class: 'err', role: 'alert', hidden: true });
    const btn = h('button', { class: 'btn primary block', type: 'button', disabled: !st.goal }, 'Дальше');
    busyButton(btn, async () => {
      try { setProfile(await api.saveProfile({ goal: st.goal })); go(next0); } catch (e) { err.textContent = e.friendly; err.hidden = false; }
    });
    return h('div', { class: 'card stack' }, back(intro), h('h2', { class: 'h2' }, 'К какой цели идём?'), h('p', { class: 'muted' }, 'Шаги подберу под неё.'),
      chipsPick(options('goals'), st.goal, (v) => { st.goal = v; btn.disabled = !v; }), err, btn);
  };

  const cityStep = () => {
    const inp = h('input', { class: 'input', placeholder: 'Например: Омск', 'aria-label': 'Город', value: st.city, autocomplete: 'address-level2' });
    const err = h('p', { class: 'err', role: 'alert', hidden: true });
    const btn = h('button', { class: 'btn primary block', type: 'button' }, 'Дальше');
    const submit = async () => {
      if (!inp.value.trim()) return inp.focus();
      try { state.boot.settings = await api.saveSettings({ city: inp.value.trim() }); go(next0); }
      catch (e) { err.textContent = e.friendly; err.hidden = false; inp.classList.add('bad'); throw e; }
    };
    busyButton(btn, () => submit().catch(() => {}));
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') btn.click(); });
    inp.addEventListener('input', () => { st.city = inp.value; });
    return h('div', { class: 'card stack' }, back(intro), h('h2', { class: 'h2' }, 'В каком ты городе?'), h('p', { class: 'muted' }, 'Нужен часовой пояс, чтобы шаги приходили в твоё время.'),
      h('div', { class: 'field' }, h('label', null, 'Город'), inp), err, btn);
  };

  const timeStep = () => {
    const tIn = h('input', { class: 'input', type: 'time', value: st.time, 'aria-label': 'Своё время' });
    const err = h('p', { class: 'err', role: 'alert', hidden: true });
    const chips = chipsPick(['08:00', '09:00', '12:00', '18:00'], st.time, (v) => { st.time = v; tIn.value = v; }, { required: true });
    tIn.addEventListener('input', () => { st.time = tIn.value; chips.querySelectorAll('.chip').forEach((c) => c.classList.toggle('on', c.textContent === st.time)); });
    const btn = h('button', { class: 'btn primary block', type: 'button' }, 'Показать план');
    busyButton(btn, async () => {
      if (!st.time) { err.textContent = 'Выбери время.'; err.hidden = false; return; }
      try {
        st.preview = await api.journeyPreview({ goal: st.goal || undefined, time: st.time }); go(previewStep);
      } catch (e) {
        if (e.code === 'need_timezone') go(cityStep); else if (e.code === 'need_goal') go(goalStep); else { err.textContent = e.friendly; err.hidden = false; }
      }
    });
    return h('div', { class: 'card stack' }, back(intro), h('h2', { class: 'h2' }, 'Во сколько присылать шаг?'), h('p', { class: 'muted' }, 'Это время можно менять. Сообщений будет по одному в день.'),
      chips, h('div', { class: 'field' }, h('label', null, 'Или своё время'), tIn), err, btn);
  };

  const previewStep = () => {
    const p = st.preview;
    const start = h('button', { class: 'btn primary block', type: 'button' }, icon('check', 18), 'Запустить на 7 дней');
    const run = async () => {
      try {
        const j = await api.journeyStart({ goal: p.goal, time: p.time, steps: p.steps });
        state.boot.journey = j; presetGoal = null; haptic('success'); toast('План запущен. Первый шаг уже сегодня'); week(host, setOff);
      } catch (e) { haptic('error'); toast(e.friendly); throw e; }
    };
    busyButton(start, () => run().catch(() => {}));
    if (tg.inTelegram) { start.hidden = true; setOff(tg.showMain('Запустить на 7 дней', () => run().catch(() => {}))); }
    return h('div', { class: 'stack-lg' },
      h('div', { class: 'card stack' }, back(timeStep), h('div', null, h('div', { class: 'eyebrow' }, 'Предпросмотр'), h('h2', { class: 'h2' }, p.goal[0].toUpperCase() + p.goal.slice(1)), h('p', { class: 'small muted' }, `Шаг в день в ${p.time} · ${p.timezone}`))),
      h('ol', { class: 'stack' }, p.steps.map((s) => h('li', { class: 'step' }, h('span', { class: 'mark num' }, s.day), h('div', { class: 'grow' }, h('div', { class: 'day' }, `День ${s.day}`), h('div', { class: 'task' }, s.task), h('div', { class: 'small muted', style: { marginTop: '2px' } }, s.title))))),
      h('div', { class: 'stack' }, h('p', { class: 'hint', style: { textAlign: 'center' } }, 'Пока ты не нажал кнопку, ничего не запускается.'), start,
        h('button', { class: 'btn text block', type: 'button', onclick: () => go(timeStep) }, 'Изменить время')));
  };

  go(intro);
}
