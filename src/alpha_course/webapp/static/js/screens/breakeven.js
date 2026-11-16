// «Точка безубыточности»: угадай, сколько продаж нужно, чтобы выйти в ноль. Считается в браузере, без ИИ.
import { h, icon, haptic } from '../ui.js';
import { ring } from '../components.js';
import { moscowDate } from '../wordle-store.js';
import { plural } from '../fmt.js';

// Учебные цифры: придуманные кейсы, не данные реальных бизнесов. Налоги не учитываются.
const CASES = [
  { id: 'coffee', title: 'Кофейня у дома', text: 'Небольшая точка с кофе навынос.', unit: ['чашка', 'чашки', 'чашек'], price: 300, cost: 100, fixed: 60000, fixedNote: 'аренда и зарплата бариста' },
  { id: 'tutor', title: 'Онлайн-репетитор', text: 'Индивидуальные уроки по видеосвязи.', unit: ['урок', 'урока', 'уроков'], price: 1500, cost: 150, fixed: 27000, fixedNote: 'реклама и сервисы' },
  { id: 'bakery', title: 'Пекарня', text: 'Свежая выпечка каждое утро.', unit: ['булочка', 'булочки', 'булочек'], price: 90, cost: 35, fixed: 110000, fixedNote: 'аренда и персонал' },
  { id: 'food', title: 'Доставка домашней еды', text: 'Обеды по подписке и разовые заказы.', unit: ['заказ', 'заказа', 'заказов'], price: 650, cost: 390, fixed: 78000, fixedNote: 'кухня, упаковка, реклама' },
  { id: 'repair', title: 'Мастерская по ремонту', text: 'Ремонт техники с выездом.', unit: ['услуга', 'услуги', 'услуг'], price: 3200, cost: 800, fixed: 72000, fixedNote: 'мастерская и инструмент' },
  { id: 'flowers', title: 'Цветочный киоск', text: 'Букеты на каждый день и под заказ.', unit: ['букет', 'букета', 'букетов'], price: 2400, cost: 1400, fixed: 50000, fixedNote: 'аренда киоска и холодильник' },
  { id: 'nails', title: 'Студия маникюра', text: 'Кабинет с двумя мастерами.', unit: ['услуга', 'услуги', 'услуг'], price: 2500, cost: 700, fixed: 90000, fixedNote: 'аренда и администратор' },
  { id: 'merch', title: 'Печать на футболках', text: 'Мерч для небольших брендов.', unit: ['футболка', 'футболки', 'футболок'], price: 1800, cost: 650, fixed: 46000, fixedNote: 'оборудование и сайт' },
];
const PER_ROUND = 3;
const KEY = 'be:';
const read = (k) => { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } };
const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* без хранилища играем в рамках сессии */ } };

export const breakevenBest = () => +read(KEY + 'best') || 0;

const num = (n) => Math.round(n).toLocaleString('ru-RU');
const rub = (n) => `${num(n)} ₽`;
const answerOf = (c) => Math.ceil(c.fixed / (c.price - c.cost));
const nice = (x) => (x < 20 ? Math.round(x) : x < 100 ? Math.round(x / 5) * 5 : x < 1000 ? Math.round(x / 10) * 10 : Math.round(x / 50) * 50);
const MIN = 5, MAX = 5000;
const toValue = (p) => nice(MIN * (MAX / MIN) ** (p / 1000));
const toPos = (v) => Math.round(1000 * Math.log(Math.max(MIN, Math.min(MAX, v)) / MIN) / Math.log(MAX / MIN));

const dayIndex = () => Math.floor(new Date(moscowDate() + 'T00:00:00Z').getTime() / 86400000);
const casesFor = (offset) => Array.from({ length: PER_ROUND }, (_, k) => CASES[(dayIndex() * PER_ROUND + offset * PER_ROUND + k) % CASES.length]);

const verdict = (err) => (err <= 0.05 ? ['В точку', 'ok', 'check'] : err <= 0.2 ? ['Близко', 'accent', 'target'] : ['Мимо', 'warn', 'alert']);

export default function mount(view, { go }) {
  const host = h('div', { class: 'stack-lg' });
  view.replaceChildren(h('div', { class: 'stack-lg' }, h('p', { class: 'muted' }, 'Угадай, сколько продаж в месяц нужно, чтобы бизнес вышел в ноль. Налоги не учитываем.'), host));

  const date = moscowDate();
  let st = read(KEY + 'state');
  if (!st || st.date !== date) st = { date, offset: 0, answers: [] };
  const save = () => write(KEY + 'state', st);
  let cases = casesFor(st.offset);

  function card() {
    if (st.answers.length >= PER_ROUND) { finish(); return; }
    const k = st.answers.length, c = cases[k];
    const right = answerOf(c);
    const unit = (n) => plural(n, ...c.unit);
    const inp = h('input', { class: 'input be-input', type: 'text', inputmode: 'numeric', autocomplete: 'off', enterkeyhint: 'done', id: 'be-guess', 'aria-describedby': 'be-q', placeholder: 'Твой ответ', maxlength: 6 });
    const slider = h('input', { class: 'be-range idle', type: 'range', min: 0, max: 1000, step: 1, value: 500, 'aria-label': 'Подбери ответ ползунком' });
    const err = h('p', { class: 'err', role: 'alert', hidden: true });
    const btn = h('button', { class: 'btn primary block', type: 'button', disabled: true }, icon('target', 18), 'Проверить');
    const readNum = () => parseInt(inp.value.replace(/\D/g, ''), 10) || 0;
    const sync = () => { slider.classList.remove('idle'); btn.disabled = readNum() < 1; err.hidden = true; };
    inp.addEventListener('input', () => { inp.value = inp.value.replace(/\D/g, '').slice(0, 6); if (readNum() > 0) slider.value = toPos(readNum()); sync(); });
    slider.addEventListener('input', () => { inp.value = String(toValue(+slider.value)); sync(); });
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !btn.disabled) { e.preventDefault(); btn.click(); } });
    btn.addEventListener('click', () => {
      const g = readNum();
      if (g < 1) { err.textContent = 'Введи число продаж больше нуля.'; err.hidden = false; return; }
      const e = Math.abs(g - right) / right;
      const pts = Math.max(0, Math.round(100 * (1 - e)));
      st.answers.push({ id: c.id, guess: g, pts }); save();
      haptic(e <= 0.2 ? 'success' : 'warning');
      reveal(c, g, right, e, pts);
    });

    host.replaceChildren(h('div', { class: 'stack-lg' },
      h('div', { class: 'mprog', role: 'img', 'aria-label': `Карточка ${k + 1} из ${PER_ROUND}` }, Array.from({ length: PER_ROUND }, (_, n) => h('i', { class: n < k ? 'ok' : n === k ? 'now' : '' }))),
      h('div', { class: 'card stack' },
        h('div', null, h('div', { class: 'eyebrow' }, `Карточка ${k + 1} из ${PER_ROUND}`), h('h2', { class: 'h2' }, c.title), h('p', { class: 'small muted' }, c.text)),
        h('dl', { class: 'befacts' },
          h('div', null, h('dt', null, 'Цена продажи'), h('dd', null, rub(c.price))),
          h('div', null, h('dt', null, 'Затраты на одну продажу'), h('dd', null, rub(c.cost))),
          h('div', null, h('dt', null, `Постоянные в месяц`, h('span', { class: 'small muted' }, ` · ${c.fixedNote}`)), h('dd', null, rub(c.fixed)))),
        h('label', { class: 'h2', for: 'be-guess', id: 'be-q' }, `Сколько ${unit(5)} в месяц нужно продать, чтобы выйти в ноль?`),
        h('div', { class: 'unit' }, inp, h('span', { 'aria-hidden': 'true' }, 'шт.')),
        h('div', null, slider, h('div', { class: 'be-scale small muted', 'aria-hidden': 'true' }, h('span', null, MIN), h('span', null, num(MAX)))),
        err, btn)));
  }

  function reveal(c, g, right, e, pts) {
    const margin = c.price - c.cost;
    const [vl, vcls, vic] = verdict(e);
    const k = st.answers.length;
    const last = k >= PER_ROUND;
    const hiPrice = Math.round(c.price * 1.1);
    const hiRight = Math.ceil(c.fixed / (hiPrice - c.cost));
    const unit = (n) => plural(n, ...c.unit);
    const diff = Math.round(e * 100);
    host.replaceChildren(h('div', { class: 'stack-lg' },
      h('div', { class: 'mprog', role: 'img', 'aria-label': `Карточка ${k} из ${PER_ROUND}` }, Array.from({ length: PER_ROUND }, (_, n) => h('i', { class: n < k ? 'ok' : '' }))),
      h('div', { class: 'card stack', 'aria-live': 'polite' },
        h('div', { class: 'row', style: { justifyContent: 'space-between' } },
          h('span', { class: 'chip static ' + vcls }, icon(vic, 14), vl),
          h('span', { class: 'small muted' }, `+${pts} из 100`)),
        h('div', null, h('div', { class: 'small muted' }, 'Нужно продать'), h('div', { class: 'fm-big' }, `${num(right)} ${unit(right)}`)),
        h('p', { class: 'small muted' }, g === right ? `Твой ответ ${num(g)} — точный.` : `Твой ответ: ${num(g)}. Разница ${diff}%.`)),
      h('div', { class: 'card stack' },
        h('h2', { class: 'h2' }, 'Как посчитать'),
        h('ol', { class: 'numl' },
          h('li', null, h('span', null, `С одной продажи остаётся ${rub(c.price)} − ${rub(c.cost)} = `, h('b', null, rub(margin)), '. Это маржа с единицы.')),
          h('li', null, h('span', null, `Постоянные расходы делим на маржу: ${rub(c.fixed)} ÷ ${rub(margin)} = `, h('b', null, `${num(right)} шт.`))))),
      h('div', { class: 'note' }, icon('bulb', 18), h('span', null, `А если поднять цену на 10%, до ${rub(hiPrice)}, точка безубыточности упадёт с ${num(right)} до ${num(hiRight)} шт.`)),
      h('button', { class: 'btn primary block', type: 'button', onclick: () => { cases = casesFor(st.offset); card(); window.scrollTo(0, 0); } }, last ? 'Посмотреть итог' : 'Следующая карточка', icon('arrowr', 18))));
  }

  function finish() {
    const avg = Math.round(st.answers.reduce((s, a) => s + a.pts, 0) / PER_ROUND);
    if (!st.counted?.[st.offset]) {
      st.counted = { ...(st.counted || {}), [st.offset]: true }; save();
      write(KEY + 'best', Math.max(breakevenBest(), avg));
      write(KEY + 'rounds', (+read(KEY + 'rounds') || 0) + 1);
    }
    const best = breakevenBest();
    host.replaceChildren(h('div', { class: 'stack-lg' },
      h('div', { class: 'card stack', style: { alignItems: 'center', textAlign: 'center' } },
        ring(avg, 100, 104, 9, String(avg)),
        h('h2', { class: 'h2' }, avg >= 80 ? 'Отличное чутьё на цифры' : avg >= 50 ? 'Неплохо, цифры стоит прикинуть точнее' : 'Формула поможет: постоянные расходы ÷ маржа'),
        h('div', { class: 'chips', style: { justifyContent: 'center' } }, h('span', { class: 'chip static' }, `Лучший раунд: ${best}/100`))),
      h('div', { class: 'stack' }, st.answers.map((a, n) => {
        const c = cases[n]; const right = answerOf(c);
        return h('div', { class: 'phrase' }, h('b', null, c.title), h('span', { class: 'small muted' }, `Твой ответ ${num(a.guess)}, верно ${num(right)}`));
      })),
      h('div', { class: 'note' }, icon('info', 18), h('span', null, 'Кейсы учебные, цифры придуманы. Для своего бизнеса посчитай точку безубыточности по настоящим данным и с налогом.')),
      h('div', { class: 'stack' },
        h('button', { class: 'btn primary block', type: 'button', onclick: () => go('tools/finmodel') }, icon('calc', 18), 'Посчитать свой бизнес в финмодели'),
        h('button', { class: 'btn soft block', type: 'button', onclick: () => { st = { date, offset: st.offset + 1, answers: [], counted: st.counted }; save(); cases = casesFor(st.offset); card(); window.scrollTo(0, 0); } }, icon('refresh', 18), 'Сыграть ещё раунд'),
        h('button', { class: 'btn text block', type: 'button', onclick: () => go('games') }, 'К списку игр'))));
  }

  card();
}
