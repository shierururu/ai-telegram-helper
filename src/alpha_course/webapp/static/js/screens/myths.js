// «Миф или правда»: 5 утверждений в день. Прогресс и серия хранятся только в браузере.
import { h, icon, haptic } from '../ui.js';
import { api } from '../api.js';
import { ring, skeleton, errorState, materialCard } from '../components.js';
import { moscowDate } from '../wordle-store.js';
import { plural } from '../fmt.js';

const KEY = 'myths:';
const read = (k) => { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } };
const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* без хранилища играем в рамках сессии */ } };
const shiftDay = (ymd, n) => { const d = new Date(ymd + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

const EMPTY = { days: 0, streak: 0, best: 0, last: null, perfect: 0 };
export const mythStats = () => ({ ...EMPTY, ...(read(KEY + 'stats') || {}) });
// Серия, которую честно показывать сегодня: если вчера/сегодня не играли — 0.
export function mythStreak() {
  const s = mythStats(); const t = moscowDate();
  return s.last === t || s.last === shiftDay(t, -1) ? s.streak : 0;
}
export const mythDoneToday = () => !!read(KEY + 'day:' + moscowDate())?.done;

function finishDay(day) {
  if (day.counted) return mythStats();
  const s = mythStats();
  s.days += 1;
  s.streak = s.last === shiftDay(day.date, -1) ? s.streak + 1 : 1;
  s.best = Math.max(s.best, s.streak);
  s.last = day.date;
  if (day.answers.every((a) => a.ok)) s.perfect += 1;
  write(KEY + 'stats', s);
  day.counted = true; write(KEY + 'day:' + day.date, day);
  return s;
}

const VERDICT = (n) => (n === 5 ? 'Безупречно' : n >= 3 ? 'Хороший результат' : 'Есть что узнать');

export default async function mount(view, { go }) {
  const host = h('div', { class: 'stack-lg' });
  view.replaceChildren(h('div', { class: 'stack-lg' }, h('p', { class: 'muted' }, 'Пять утверждений о бизнесе. Скажи, что это, и сразу узнаешь почему.'), host));
  host.replaceChildren(skeleton('card', 1));
  let info;
  const load = async () => {
    try { info = await api.myths(); } catch (e) { host.replaceChildren(errorState(e, load)); return; }
    start();
  };

  let day, i;
  function start() {
    const saved = read(KEY + 'day:' + info.date);
    const cards = info.cards;
    day = saved && saved.answers ? saved : { date: info.date, answers: [], done: false };
    day.answers = day.answers.slice(0, cards.length);
    if (day.done || day.answers.length >= cards.length) { day.done = true; finish(); return; }
    i = day.answers.length;
    question();
  }

  const progress = () => h('div', { class: 'mprog', role: 'img', 'aria-label': `Вопрос ${Math.min(i + 1, info.cards.length)} из ${info.cards.length}` },
    info.cards.map((_, k) => h('i', { class: k < day.answers.length ? (day.answers[k].ok ? 'ok' : 'miss') : k === i ? 'now' : '' })));

  function question() {
    const c = info.cards[i];
    const pick = (guess) => {
      const ok = guess === c.truth;
      day.answers.push({ id: c.id, guess, ok });
      write(KEY + 'day:' + day.date, day);
      haptic(ok ? 'success' : 'warning');
      answer(c, guess, ok);
    };
    host.replaceChildren(h('div', { class: 'stack-lg' }, progress(),
      h('div', { class: 'card mcard stack' },
        h('div', { class: 'eyebrow' }, `Утверждение ${i + 1} из ${info.cards.length}`),
        h('p', { class: 'mstate', id: 'm-q' }, c.statement)),
      h('div', { class: 'mbtns', role: 'group', 'aria-labelledby': 'm-q' },
        h('button', { class: 'btn mbtn myth', type: 'button', onclick: () => pick(false) }, icon('close', 20), 'Миф'),
        h('button', { class: 'btn mbtn truth', type: 'button', onclick: () => pick(true) }, icon('check', 20), 'Правда'))));
  }

  function answer(c, guess, ok) {
    const last = i === info.cards.length - 1;
    const next = h('button', { class: 'btn primary block', type: 'button', onclick: () => { if (last) { day.done = true; write(KEY + 'day:' + day.date, day); finish(); } else { i += 1; question(); window.scrollTo(0, 0); } } },
      last ? 'Посмотреть итог' : 'Дальше', icon('arrowr', 18));
    host.replaceChildren(h('div', { class: 'stack-lg' }, progress(),
      h('div', { class: 'card mcard stack ' + (ok ? 'is-ok' : 'is-miss'), 'aria-live': 'polite' },
        h('span', { class: 'chip static ' + (ok ? 'ok' : 'warn') }, icon(ok ? 'check' : 'alert', 14), ok ? 'Верно' : 'Не совсем'),
        h('p', { class: 'mstate' }, c.statement),
        h('p', { class: 'mverdict' }, c.truth ? 'Это правда.' : 'Это миф.'),
        h('p', { class: 'mwhy' }, c.why)),
      c.material ? h('div', { class: 'stack' }, h('h2', { class: 'h2' }, 'Подробнее в базе знаний'), materialCard(c.material)) : null,
      h('p', { class: 'hint' }, 'Общие сведения для ориентира. Законы меняются, поэтому детали проверяй в материале или у бухгалтера.'),
      next));
    next.focus({ preventScroll: true });
  }

  function finish() {
    const s = finishDay(day);
    const total = info.cards.length;
    const good = day.answers.filter((a) => a.ok).length;
    const missed = info.cards.filter((_, k) => day.answers[k] && !day.answers[k].ok);
    host.replaceChildren(h('div', { class: 'stack-lg' },
      h('div', { class: 'card stack', style: { alignItems: 'center', textAlign: 'center' } },
        ring(good, total, 104, 9, `${good}/${total}`),
        h('h2', { class: 'h2' }, VERDICT(good)),
        h('div', { class: 'chips', style: { justifyContent: 'center' } },
          s.streak > 1 ? h('span', { class: 'chip static accent' }, icon('flame', 14), `${s.streak} ${plural(s.streak, 'день', 'дня', 'дней')} подряд`) : null,
          h('span', { class: 'chip static' }, `Всего игр: ${s.days}`)),
        h('p', { class: 'small muted' }, 'Новые утверждения появятся завтра.')),
      missed.length ? h('div', { class: 'stack' }, h('h2', { class: 'h2' }, 'Над чем подумать'),
        missed.map((c) => h('div', { class: 'phrase' }, h('b', null, c.statement), h('span', { class: 'small muted' }, (c.truth ? 'Правда. ' : 'Миф. ') + c.why)))) : null,
      h('div', { class: 'stack' },
        h('button', { class: 'btn primary block', type: 'button', onclick: () => go('games/breakeven') }, icon('calc', 18), 'Сыграть в «Точку безубыточности»'),
        h('button', { class: 'btn text block', type: 'button', onclick: () => go('games') }, 'К списку игр'))));
  }

  load();
}
