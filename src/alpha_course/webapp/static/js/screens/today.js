// «Сегодня»: шаг маршрута, ближайший налоговый срок, быстрый вопрос, напоминания.
import { h, icon, haptic, toast, busyButton } from '../ui.js';
import { api } from '../api.js';
import { state, completeness, refreshReminders } from '../state.js';
import { todayLine, parseYmd, daysUntil, inDays, monthShort, whenText, FREQ_SHORT } from '../fmt.js';
import { streakNow, playedToday } from '../wordle-store.js';
import { openMaterial, section, skeleton } from '../components.js';

function toolsRow() {
  return h('a', { class: 'card tight row pressable', href: '#/tools', style: { textDecoration: 'none' } },
    h('div', { class: 'datebox' }, icon('tools', 24)),
    h('div', { class: 'grow' }, h('div', { style: { fontWeight: 600 } }, 'Инструменты'), h('div', { class: 'small muted' }, 'Финмодель, игры и налоговый календарь')),
    h('span', { class: 'chev muted' }, icon('chev', 20)));
}

function wordleCard() {
  const st = streakNow(), played = playedToday();
  return h('a', { class: 'card tight row pressable', href: '#/wordle', style: { textDecoration: 'none' } },
    h('div', { class: 'datebox' }, icon('word', 24)),
    h('div', { class: 'grow' }, h('div', { style: { fontWeight: 600 } }, 'Слово дня'),
      h('div', { class: 'small muted' }, played ? 'Сегодня уже сыграно. Новое слово завтра' : 'Угадай бизнес-термин за 6 попыток')),
    st > 0 ? h('span', { class: 'streak', 'aria-label': `Серия: ${st}` }, icon('flame', 14), st) : null,
    h('span', { class: 'chev muted' }, icon('chev', 20)));
}

const SUGGEST = ['Как выбрать налоговый режим?', 'Где взять первых клиентов?', 'Что такое эквайринг?', 'Какие документы нужны для ИП?'];

function greeting() {
  const hr = new Date().getHours();
  return hr < 5 ? 'Доброй ночи' : hr < 12 ? 'Доброе утро' : hr < 18 ? 'Добрый день' : 'Добрый вечер';
}

export default async function mount(view, { go }) {
  const b = state.boot;
  const askNow = (q) => { state.pendingQuestion = q; go('ask'); };

  const journeyHost = h('div');
  const taxHost = h('div');
  const remHost = h('div');

  // ---- быстрый вопрос
  const inp = h('input', { class: 'input', placeholder: 'Задай вопрос о бизнесе', 'aria-label': 'Твой вопрос', enterkeyhint: 'send' });
  const form = h('form', { class: 'row', onsubmit: (e) => { e.preventDefault(); if (inp.value.trim()) askNow(inp.value.trim()); } },
    inp, h('button', { class: 'send', type: 'submit', 'aria-label': 'Спросить' }, icon('send', 22)));
  const ask = h('section', { class: 'card stack', 'aria-labelledby': 'qa-t' },
    h('h2', { class: 'h2', id: 'qa-t' }, 'Спросить помощника'), form,
    h('div', { class: 'chips' }, SUGGEST.slice(0, 3).map((q) => h('button', { class: 'chip', type: 'button', onclick: () => askNow(q) }, q))));

  // ---- профиль
  const { n, total } = completeness();
  let dismissed = false;
  try { dismissed = localStorage.getItem('nudge') === new Date().toDateString(); } catch (e) { /* noop */ }
  const nudge = n < total && !dismissed ? h('div', { class: 'nudge' },
    h('a', { href: '#/profile', class: 'grow', style: { textDecoration: 'none' } },
      h('div', { class: 'small', style: { fontWeight: 600 } }, `Профиль заполнен на ${n} из ${total}`),
      h('div', { class: 'small muted' }, 'Чуть больше сведений — и советы станут точнее'),
      h('div', { class: 'pbar', style: { marginTop: '8px' } }, h('i', { style: { '--p': Math.round((n / total) * 100) + '%' } }))),
    h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Скрыть до завтра', onclick: (e) => {
      try { localStorage.setItem('nudge', new Date().toDateString()); } catch (er) { /* noop */ }
      e.currentTarget.closest('.nudge').remove();
    } }, icon('close', 18))) : null;

  view.replaceChildren(
    h('div', { class: 'page-head' }, h('div', { class: 'eyebrow' }, todayLine()), h('h1', { class: 'h1' }, greeting() + '!')),
    h('div', { class: 'stack-lg' }, journeyHost, taxHost, wordleCard(), toolsRow(), ask, remHost, nudge));

  // ---- журнал: шаг дня
  function drawJourney() {
    const j = state.boot.journey && state.boot.journey.status === 'active' ? state.boot.journey : null;
    if (!j) {
      journeyHost.replaceChildren(h('section', { class: 'hero' },
        h('div', null, h('div', { class: 'eyebrow' }, 'План на 7 дней'), h('h2', { class: 'h2', style: { marginTop: '4px', fontSize: 'var(--t-xl)', lineHeight: 1.25 } }, 'Один маленький шаг в день к твоей цели')),
        h('p', { class: 'muted' }, 'Подберу семь коротких заданий по материалам и напомню в удобное время. Запускается только если ты сам этого хочешь.'),
        h('a', { class: 'btn primary', href: '#/route' }, 'Составить план')));
      return;
    }
    const today = j.steps.find((s) => s.state === 'today');
    const missed = j.steps.filter((s) => s.state === 'missed').length;
    const upcoming = j.steps.find((s) => s.state === 'locked');
    const step = today || j.steps.find((s) => s.state === 'missed');
    const dayNo = step ? step.day : (upcoming ? upcoming.day - 1 : j.steps.length);
    const bars = h('div', { class: 'bars', role: 'img', 'aria-label': `Выполнено ${j.done_days} из ${j.steps.length}` },
      j.steps.map((s) => h('i', { class: s.done ? 'done' : s.state === 'today' ? 'now' : '' })));
    const head = h('div', { class: 'row', style: { justifyContent: 'space-between' } },
      h('span', { class: 'small', style: { fontWeight: 600, color: 'var(--hero-ink-2)' } }, `Маршрут · день ${dayNo} из ${j.steps.length}`),
      h('span', { class: 'small', style: { color: 'var(--hero-ink-2)' } }, j.time));
    if (!step || step.done) {
      journeyHost.replaceChildren(h('section', { class: 'hero' }, head, bars,
        h('h2', { class: 'h2', style: { fontSize: 'var(--t-xl)', lineHeight: 1.25 } }, upcoming ? 'На сегодня всё сделано' : 'План выполнен'),
        h('p', { class: 'muted' }, upcoming ? `Следующий шаг — ${whenText(upcoming.due)}: ${upcoming.task}` : 'Отличная неделя. Можно составить новый план.'),
        h('a', { class: 'btn on-dark', href: '#/route' }, 'Весь план')));
      return;
    }
    const doneBtn = h('button', { class: 'btn on-dark', type: 'button' }, icon('check', 18), 'Готово');
    busyButton(doneBtn, async () => {
      try {
        state.boot.journey = await api.journeyDone(step.day); haptic('success'); toast('Шаг выполнен. Так держать!'); drawJourney();
      } catch (e) { haptic('error'); toast(e.friendly); }
    });
    journeyHost.replaceChildren(h('section', { class: 'hero', 'aria-label': 'Шаг на сегодня' }, head, bars,
      h('h2', { style: { fontSize: 'var(--t-xl)', lineHeight: 1.25, fontWeight: 600, letterSpacing: '-0.015em' } }, step.task),
      h('p', { class: 'small muted' }, `Материал: «${step.title}»` + (missed && step.state === 'missed' ? ' · пропущенный день' : '')),
      h('div', { class: 'actions' },
        h('button', { class: 'btn primary grow', type: 'button', onclick: () => openMaterial({ id: step.material_id, url: step.url }) }, 'Открыть материал'), doneBtn)));
  }
  drawJourney();

  // ---- налоги
  function taxCard(inner) { return h('a', { class: 'card tight row pressable', href: '#/taxes', style: { textDecoration: 'none' } }, inner, h('span', { class: 'chev muted' }, icon('chev', 20))); }
  async function drawTax() {
    if (!b.taxes_ready) {
      taxHost.replaceChildren(taxCard([h('div', { class: 'datebox' }, icon('calendar', 22)),
        h('div', { class: 'grow' }, h('div', { class: 'small muted', style: { fontWeight: 600 } }, 'Налоговый календарь'), h('div', { style: { fontWeight: 600 } }, 'Узнай ближайший срок'), h('div', { class: 'small muted' }, 'Нужно три коротких ответа о бизнесе'))]));
      return;
    }
    taxHost.replaceChildren(skeleton('card', 1));
    try {
      const t = await api.taxes();
      if (!t.ready || !t.next) { taxHost.replaceChildren(); return; }
      const d = parseYmd(t.next.date);
      taxHost.replaceChildren(taxCard([
        h('div', { class: 'datebox' }, h('small', null, monthShort(d)), h('b', null, d.getDate())),
        h('div', { class: 'grow' }, h('div', { class: 'small muted', style: { fontWeight: 600 } }, `Ближайший срок · ${inDays(daysUntil(d))}`), h('div', { style: { fontWeight: 600, lineHeight: 1.3 } }, t.next.title), h('div', { class: 'small muted' }, 'Напомню за 3 дня — включить можно в календаре'))]));
    } catch (e) { taxHost.replaceChildren(); }
  }
  drawTax();

  // ---- напоминания
  function drawRem() {
    const list = (state.boot.reminders || []).slice(0, 2);
    remHost.replaceChildren(section('Напоминания', h('a', { href: '#/reminders', class: 'link' }, list.length ? 'Все' : 'Добавить')),
      list.length ? h('div', { class: 'list' }, list.map((r) => h('a', { class: 'item', href: '#/reminders' },
        h('div', { class: 'ico' }, icon(r.frequency === 'once' ? 'bell' : 'repeat', 20)),
        h('div', { class: 'grow' }, h('div', { class: 't' }, r.event), h('div', { class: 's' }, r.timing)))))
        : h('a', { class: 'card tight row pressable', href: '#/reminders', style: { textDecoration: 'none' } }, h('div', { class: 'item', style: { padding: 0, minHeight: 0 } }, h('div', { class: 'ico' }, icon('bell', 20)), h('div', { class: 'grow' }, h('div', { class: 't' }, 'Пока нет напоминаний'), h('div', { class: 's' }, 'Попроси напомнить об учёбе или важном деле')))));
  }
  drawRem();

  // тихо освежаем данные
  try {
    const [j, ] = await Promise.all([api.journey(), refreshReminders()]);
    state.boot.journey = j; drawJourney(); drawRem();
  } catch (e) { /* остаёмся на данных bootstrap */ }
}
