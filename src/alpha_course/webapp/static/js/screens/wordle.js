// «Слово дня»: ежедневная игра с деловыми терминами. Состояние и статистика только в браузере.
import { h, icon, haptic, toast, openSheet } from '../ui.js';
import { api } from '../api.js';
import { tg } from '../tg.js';
import { skeleton, errorState, openMaterial } from '../components.js';
import { loadDay, saveDay, recordFinish, loadStats, streakNow, secondsToMidnight, fmtLeft } from '../wordle-store.js';

const ROWS = ['ЙЦУКЕНГШЩЗХЪ', 'ФЫВАПРОЛДЖЭ', 'ЯЧСМИТЬБЮ'];
const LATIN = Object.fromEntries([...'qwertyuiop[]asdfghjkl;\'zxcvbnm,.'].map((c, i) => [c, [...'йцукенгшщзхъфывапролджэячсмитьбю'][i]]));
const RANK = { absent: 1, present: 2, correct: 3 };
const SQ = { correct: '🟩', present: '🟨', absent: '⬛' };
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const mark = { correct: 'верно', present: 'есть в слове, но не здесь', absent: 'нет в слове' };

export default async function mount(view, { go, back: goBack }) {
  const host = h('div', { class: 'wscreen' });
  view.replaceChildren(host);
  const topBack = h('button', { class: 'icon-btn back', type: 'button', 'aria-label': 'Назад', onclick: goBack }, icon('back', 24));
  host.replaceChildren(h('div', { class: 'whead' }, topBack, h('h1', null, 'Слово дня')), h('div', { class: 'wmid' }, skeleton('card', 1)));
  let info;
  try { info = await api.wordle(); } catch (e) { host.replaceChildren(h('div', { class: 'whead' }, topBack, h('h1', null, 'Слово дня')), errorState(e, () => mount(view, { go, back: goBack }))); return; }

  const n = info.length, max = info.max_tries;
  const day = loadDay(info.date) || { date: info.date, number: info.number, guesses: [], finished: false, solved: false };
  let cur = [], busy = false, timer = null;

  // ---- сетка
  const tiles = Array.from({ length: max }, () => Array.from({ length: n }, () => h('div', { class: 'wt', role: 'img' })));
  const rowEls = tiles.map((r, i) => h('div', { class: 'wrow', role: 'group', 'aria-label': `Попытка ${i + 1}` }, r));
  const grid = h('div', { class: 'wgrid', style: { '--n': n } }, rowEls);
  const mid = h('div', { class: 'wmid' }, grid);
  const fit = () => { const w = mid.clientWidth, hh = mid.clientHeight; if (!w || !hh) return; const gap = 5; const t = Math.floor(Math.min((w - (n - 1) * gap) / n, (hh - (max - 1) * gap) / max, 64)); grid.style.setProperty('--tile', Math.max(28, t) + 'px'); };
  if (window.ResizeObserver) new ResizeObserver(fit).observe(mid); addEventListener('resize', fit);
  const msg = h('div', { class: 'wmsg', role: 'status', 'aria-live': 'polite' });
  const say = (t, bad) => { msg.textContent = t || ''; msg.classList.toggle('bad', !!bad); };

  const paint = (t, ch, s) => {
    t.textContent = ch || ''; t.classList.toggle('filled', !!ch && !s);
    if (s) t.dataset.s = s; else delete t.dataset.s;
    t.setAttribute('aria-label', ch ? (s ? `${ch}, ${mark[s]}` : ch) : 'пусто');
  };

  // ---- клавиатура
  const keyEls = {};
  const keyState = () => {
    const m = {};
    for (const g of day.guesses) [...g.word].forEach((c, i) => { if (!m[c] || RANK[g.result[i]] > RANK[m[c]]) m[c] = g.result[i]; });
    return m;
  };
  const refreshKeys = () => { const m = keyState(); for (const [c, el] of Object.entries(keyEls)) { if (m[c]) { el.dataset.s = m[c]; el.setAttribute('aria-label', `${c}, ${mark[m[c]]}`); } } };
  const keyBtn = (label, cls, fn, aria) => h('button', { class: 'wk ' + cls, type: 'button', 'aria-label': aria || label, onclick: () => { haptic('light'); fn(); } }, label);
  const kb = h('div', { class: 'wkeys', role: 'group', 'aria-label': 'Клавиатура' }, ROWS.map((r, ri) => h('div', { class: 'wkrow' },
    ri === 2 ? keyBtn('Ввод', 'wide enter', submit) : null,
    [...r].map((c) => { const b = keyBtn(c, '', () => type(c)); keyEls[c] = b; return b; }),
    ri === 2 ? keyBtn('⌫', 'wide', back, 'Стереть') : null)));

  function rowCur() { return day.guesses.length; }
  function redrawCur() { const r = rowCur(); if (r >= max) return; tiles[r].forEach((t, i) => paint(t, cur[i])); }
  function type(c) { if (busy || day.finished || cur.length >= n) return; cur.push(c); say(''); redrawCur(); }
  function back() { if (busy || day.finished) return; cur.pop(); say(''); redrawCur(); }
  function shake() { const el = rowEls[rowCur()]; el.classList.remove('shake'); void el.offsetWidth; el.classList.add('shake'); haptic('error'); }

  async function submit() {
    if (busy || day.finished) return;
    if (cur.length < n) { shake(); say(`Нужно слово из ${n} букв`, true); return; }
    const word = cur.join(''); const r = rowCur();
    busy = true;
    let res;
    try { res = await api.wordleGuess(word, r + 1); }
    catch (e) {
      busy = false;
      if (e.code === 'invalid_word' || e.status === 400) { shake(); say(e.message || `Нужно слово из ${n} русских букв.`, true); }
      else { toast(e.friendly); }
      return;
    }
    say('');
    day.guesses.push({ word, result: res.result });
    cur = [];
    await reveal(r, word, res.result, true);
    refreshKeys();
    if (res.finished) {
      Object.assign(day, { finished: true, solved: res.solved, answer: res.answer, definition: res.definition, material: res.material });
      recordFinish(day);
      renderDone();
      if (res.solved) { haptic('success'); if (!reduced()) tiles[r].forEach((t, i) => setTimeout(() => { t.classList.remove('flip'); t.classList.add('win'); }, i * 80)); } else haptic('warning');
      await sleep(res.solved && !reduced() ? 900 : 350);
      busy = false; showResult();
    } else { saveDay(day); busy = false; }
  }

  async function reveal(r, word, result, animate) {
    for (let i = 0; i < n; i++) {
      const t = tiles[r][i]; paint(t, word[i], null);
      const apply = () => paint(t, word[i], result[i]);
      if (animate && !reduced()) {
        setTimeout(() => { t.classList.add('flip'); haptic('light'); }, i * 200);
        setTimeout(apply, i * 200 + 250);
      } else apply();
    }
    if (animate && !reduced()) await sleep((n - 1) * 200 + 520);
  }

  // ---- верхняя панель, подсказка, итоговые блоки
  const streakChip = h('span', { class: 'streak', hidden: true }, icon('flame', 14), h('span'));
  const refreshStreak = () => { const st = streakNow(); streakChip.hidden = st < 1; streakChip.querySelector('span').textContent = st; streakChip.setAttribute('aria-label', `Серия: ${st}`); };
  const openHint = () => openSheet({ title: 'Подсказка', build: () => h('div', { class: 'stack' },
    h('p', { class: 'mask' }, info.hint), h('p', { class: 'hint' }, 'Подсказка бесплатная — на результат она не влияет.')) });
  const head = h('div', { class: 'whead' }, topBack,
    h('h1', null, `Слово дня №${info.number}`, h('span', { class: 'sub' }, ` · ${n} букв`)),
    streakChip,
    h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Показать подсказку', onclick: openHint }, icon('bulb', 22)),
    h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Статистика', onclick: () => showResult() }, icon('chart', 22)));

  const done = h('div', { class: 'wdone', hidden: true });
  const cd = h('span');
  function renderDone() {
    kb.hidden = true; done.hidden = false;
    done.replaceChildren(h('div', { class: 'grow' }, h('div', { style: { fontWeight: 700 } }, day.solved ? `Угадано за ${day.guesses.length} из ${max}` : 'Сегодня не получилось'), h('div', { class: 'small muted' }, 'Новое слово через ', cd)),
      h('button', { class: 'btn primary', type: 'button', onclick: () => showResult() }, 'Результат'));
    refreshStreak();
  }
  const tick = () => { const s = secondsToMidnight(); cd.textContent = fmtLeft(s); document.querySelectorAll('.cdown').forEach((e) => { e.textContent = fmtLeft(s); }); };
  timer = setInterval(tick, 1000); tick();

  // ---- результат
  function shareText() {
    const rows = day.guesses.map((g) => g.result.map((x) => SQ[x]).join('')).join('\n');
    return `Слово дня №${day.number} ${day.solved ? day.guesses.length : 'X'}/${max}\n${rows}\nБизнес-навигатор`;
  }
  async function share() {
    const text = shareText();
    try {
      if (navigator.share && matchMedia('(pointer: coarse)').matches) { await navigator.share({ text }); return; }
      await navigator.clipboard.writeText(text); toast('Результат скопирован');
    } catch (e) { /* отмена */ }
  }
  function showResult() {
    const fin = day.finished;
    const s = loadStats();
    const wins = s.games ? Math.round((s.wins / s.games) * 100) : 0;
    const maxd = Math.max(1, ...s.dist);
    openSheet({ title: !fin ? 'Статистика' : day.solved ? 'Верно!' : 'Не в этот раз', build: () => {
      const simple = h('div', { class: 'simple', hidden: true, 'aria-live': 'polite' });
      const sbtn = h('button', { class: 'btn soft block', type: 'button', onclick: async (e) => {
        const b = e.currentTarget; b.disabled = true;
        simple.hidden = false; simple.replaceChildren(h('div', { class: 'skel', style: { height: '14px', width: '80%' } }));
        try {
          const r = await api.simplify(`что такое ${day.answer.toLowerCase()}`, day.definition);
          if (!r.text) { simple.hidden = true; toast('Проще не получилось.'); b.disabled = false; return; }
          simple.replaceChildren(h('div', { class: 'blk-t' }, 'Проще говоря'), h('p', null, r.text),
            r.terms?.length ? h('div', { class: 'terms' }, r.terms.map((t) => h('div', null, h('b', null, t.term), ' — ', t.meaning))) : null);
        } catch (er) { simple.hidden = true; toast(er.friendly); b.disabled = false; }
      } }, icon('bulb', 18), 'Объяснить проще');
      const statBlock = h('div', { class: 'stack' },
          h('div', { class: 'statrow' }, [[s.games, 'сыграно'], [wins + '%', 'побед'], [s.cur, 'серия'], [s.best, 'рекорд']].map(([v, l]) => h('div', null, h('b', null, v), h('span', null, l)))),
          h('div', { class: 'dist', role: 'list', 'aria-label': 'Распределение побед по попыткам' }, s.dist.map((c, i) => h('div', { role: 'listitem' }, h('span', null, i + 1), h('b', { class: day.solved && day.guesses.length === i + 1 ? 'me' : '', style: { '--p': Math.max(8, (c / maxd) * 100) + '%' } }, c)))));
      if (!fin) return h('div', { class: 'stack-lg' }, statBlock, h('p', { class: 'small muted', style: { textAlign: 'center' } }, 'Статистика хранится только на этом устройстве.'));
      return h('div', { class: 'stack-lg' },
        h('div', { class: 'stack' }, h('div', { class: 'answer', 'aria-label': `Ответ: ${day.answer}` }, [...day.answer].map((c) => h('i', null, c))),
          h('p', { class: 'small muted', style: { textAlign: 'center' } }, day.solved ? `Угадано с ${day.guesses.length}-й попытки` : 'Это было слово дня')),
        h('div', null, h('div', { class: 'blk-t', style: { fontSize: 'var(--t-xs)', fontWeight: 700, color: 'var(--ink-2)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: '6px' } }, 'Что это значит'), h('p', null, day.definition)),
        simple,
        h('div', { class: 'stack' },
          day.material ? h('a', { class: 'btn line block', href: day.material.url, onclick: (e) => { e.preventDefault(); openMaterial(day.material); } }, icon('book', 18), 'Читать в базе') : null,
          sbtn),
        statBlock,
        h('div', { class: 'stack' },
          h('button', { class: 'btn primary block', type: 'button', onclick: share }, icon('share', 18), 'Поделиться'),
          h('p', { class: 'small muted', style: { textAlign: 'center' } }, 'Следующее слово через ', h('b', { class: 'cdown' }, fmtLeft(secondsToMidnight())))));
    } });
  }

  // ---- сборка
  host.replaceChildren(head, mid, msg, h('div', { class: 'wbottom' }, kb, done));
  fit(); refreshStreak();
  for (let r = 0; r < day.guesses.length; r++) await reveal(r, day.guesses[r].word, day.guesses[r].result, false);
  refreshKeys();
  if (day.finished) renderDone();

  // физическая клавиатура
  const onKey = (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || document.querySelector('.sheet') || /INPUT|TEXTAREA/.test(e.target.tagName)) return;
    if (e.key === 'Enter') { e.preventDefault(); submit(); return; }
    if (e.key === 'Backspace') { back(); return; }
    let c = e.key.length === 1 ? e.key.toLowerCase() : '';
    c = LATIN[c] || c;
    if (c === 'ё') c = 'е';
    if (/^[а-я]$/.test(c)) type(c.toUpperCase());
  };
  document.addEventListener('keydown', onKey);
  void tg; void go;
  return () => { document.removeEventListener('keydown', onKey); removeEventListener('resize', fit); clearInterval(timer); };
}
