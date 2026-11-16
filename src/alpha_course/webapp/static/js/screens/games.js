// «Игры»: хаб игр и тренажёров.
import { h, icon } from '../ui.js';
import { pageHead } from '../components.js';
import { streakNow, playedToday } from '../wordle-store.js';
import { mythStreak, mythDoneToday } from './myths.js';
import { breakevenBest } from './breakeven.js';

const ls = (k) => { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } };

const ART = {
  wordle: '<rect x="8" y="8" width="24" height="24" rx="6" class="a-ok"/><rect x="36" y="8" width="24" height="24" rx="6" class="a-mid"/><rect x="64" y="8" width="24" height="24" rx="6" class="a-off"/><rect x="8" y="36" width="24" height="24" rx="6" class="a-off"/><rect x="36" y="36" width="24" height="24" rx="6" class="a-ok"/><rect x="64" y="36" width="24" height="24" rx="6" class="a-mid"/><rect x="8" y="64" width="24" height="24" rx="6" class="a-mid"/><rect x="36" y="64" width="24" height="24" rx="6" class="a-off"/><rect x="64" y="64" width="24" height="24" rx="6" class="a-ok"/><path d="M15 20l3 3 6-6M71 76l3 3 6-6M43 48l3 3 6-6" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>',
  negotiation: '<path d="M14 18a8 8 0 0 1 8-8h32a8 8 0 0 1 8 8v16a8 8 0 0 1-8 8H36l-12 10V42h-2a8 8 0 0 1-8-8z" class="a-ok"/><path d="M34 52a8 8 0 0 1 8-8h32a8 8 0 0 1 8 8v16a8 8 0 0 1-8 8h-2v10L60 76H42a8 8 0 0 1-8-8z" class="a-line"/><path d="M26 26h28M26 34h16" stroke="#fff" stroke-width="3" stroke-linecap="round" fill="none"/><circle cx="48" cy="60" r="3" class="a-fill"/><circle cx="58" cy="60" r="3" class="a-fill"/><circle cx="68" cy="60" r="3" class="a-fill"/>',
  idea: '<path d="M48 10a22 22 0 0 0-12 40.4c2 1.4 3 3.4 3 5.6v2h18v-2c0-2.2 1-4.2 3-5.6A22 22 0 0 0 48 10z" class="a-ok"/><path d="M38 66h20M41 74h14M44 82h8" stroke-width="4" stroke-linecap="round" class="a-stroke"/><path d="M38 31l7 7 13-14" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>',
};
ART.myths = '<rect x="8" y="18" width="38" height="52" rx="9" class="a-line"/><rect x="50" y="26" width="38" height="52" rx="9" class="a-ok"/><path d="M20 36l14 14M34 36L20 50" fill="none" stroke-width="4.5" stroke-linecap="round" class="a-stroke"/><path d="M62 52l7 7 13-15" fill="none" stroke="#fff" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>';
ART.breakeven = '<path d="M14 12v70h70" fill="none" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" class="a-stroke"/><path d="M20 76L82 20" fill="none" stroke-width="4.5" stroke-linecap="round" class="a-stroke"/><path d="M20 52l62-12" fill="none" stroke-width="4.5" stroke-linecap="round" stroke-dasharray="2 9" class="a-stroke"/><circle cx="54" cy="46" r="7" class="a-fill"/><circle cx="54" cy="46" r="3" fill="#fff"/>';
const art = (k) => { const s = h('svg', { viewBox: '0 0 96 96', width: 76, height: 76, 'aria-hidden': 'true', focusable: 'false' }); s.innerHTML = ART[k]; return s; };

export default function mount(view) {
  const streak = streakNow();
  const best = Math.max(0, ...Object.values(ls('neg:best') || { x: 0 }));
  const mStreak = mythStreak();
  const bBest = breakevenBest();
  const ideas = +localStorage.getItem('idea:count') || 0;
  const chip = (t, cls = '') => h('span', { class: 'chip static ' + cls }, t);
  const card = (key, href, title, text, chips) => h('a', { class: `gcard pressable g-${key}`, href },
    h('div', { class: 'gart' }, art(key)),
    h('div', { class: 'grow stack', style: { gap: '6px' } }, h('h2', { class: 'h2' }, title), h('p', { class: 'small muted' }, text), chips.length ? h('div', { class: 'chips' }, chips) : null),
    h('span', { class: 'chev muted' }, icon('chev', 20)));

  view.replaceChildren(
    pageHead('Игры', 'Игры и тренажёры', 'Короткие практики по темам бизнеса. Ссылки ведут в базу знаний, а цифры в учебных кейсах придуманы.'),
    h('div', { class: 'stack' },
      card('wordle', '#/wordle', 'Слово дня', 'Угадай бизнес-термин за 6 попыток. Новое слово каждый день.', [playedToday() ? chip('сыграно сегодня', 'ok') : chip('ждёт тебя', 'accent'), streak > 0 ? chip(`серия ${streak}`) : null]),
      card('myths', '#/games/myths', 'Миф или правда', 'Пять утверждений о бизнесе каждый день. Узнай, что правда, а что распространённый миф.', [mythDoneToday() ? chip('сыграно сегодня', 'ok') : chip('5 вопросов', 'accent'), mStreak > 1 ? chip(`серия ${mStreak}`) : null]),
      card('breakeven', '#/games/breakeven', 'Точка безубыточности', 'Угадай, сколько продаж нужно, чтобы выйти в ноль. Три учебных кейса за раз.', [bBest > 0 ? chip(`лучший раунд ${bBest}/100`, 'ok') : chip('1–2 минуты')]),
      card('negotiation', '#/games/negotiation', 'Переговоры', 'Потренируйся договариваться с ИИ-собеседником: 6 сценариев.', [chip('6 сценариев'), best > 0 ? chip(`лучший результат ${best}/100`, 'ok') : null]),
      card('idea', '#/games/idea', 'Проверка идеи', 'Оцени бизнес-идею по базе знаний: документы, налоги, риски.', [ideas > 0 ? chip(`проверено идей: ${ideas}`) : chip('1–2 минуты')])),
    h('p', { class: 'hint', style: { marginTop: 'var(--s-4)' } }, 'Результаты и серии хранятся только на этом устройстве.'));
}
