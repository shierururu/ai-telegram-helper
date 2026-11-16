// «Переговоры»: тренажёр с ИИ-собеседником.
import { h, icon, haptic, toast, openSheet, confirmSheet } from '../ui.js';
import { api } from '../api.js';
import { tg } from '../tg.js';
import { ring, skeleton, errorState, materialCard } from '../components.js';

const ss = { get(k) { try { return JSON.parse(sessionStorage.getItem(k)); } catch (e) { return null; } }, set(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* noop */ } } };
const best = { get: () => { try { return JSON.parse(localStorage.getItem('neg:best')) || {}; } catch (e) { return {}; } }, put: (id, sc) => { try { const b = best.get(); if (!(b[id] >= sc)) { b[id] = sc; localStorage.setItem('neg:best', JSON.stringify(b)); } } catch (e) { /* noop */ } } };
const OUT = { deal: ['Сделка', 'ok', 'check'], no_deal: ['Без сделки', 'warn', 'help'], walkout: ['Переговоры сорваны', 'accent', 'alert'] };
const TIPS = ['Сначала выясни интересы собеседника, потом предлагай варианты.', 'Называй конкретные сроки и суммы — расплывчатость ослабляет позицию.', 'Предлагай обмен: уступка за уступку.', 'Не давай ультиматумов — оставляй собеседнику выход.'];
const moodWord = (m) => (m < 20 ? 'холодный' : m < 40 ? 'насторожен' : m < 60 ? 'нейтрален' : m < 80 ? 'расположен' : 'доброжелателен');
const moodColor = (m) => `color-mix(in oklab, #3B6FD9 ${100 - m}%, #F08A24)`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export default async function mount(view, { go, back: goBack }) {
  const root = h('div', { class: 'nscreen' });
  view.replaceChildren(root);
  let sc = null, hist = [], mood = 40, max = 6, tip = null, finished = false, outcome = null, busy = false, scenarios = null;
  let offBack = () => {}, typingEl = null, resultShown = false;

  const setBack = (fn) => { offBack(); offBack = fn ? tg.pushBack(fn) : () => {}; };
  const saveDraft = () => ss.set('neg:draft', sc ? { sc, hist, mood, max, tip, finished, outcome } : null);
  const myTurns = () => hist.filter((x) => x.role === 'me').length;

  const header = (title, onBack, extra) => h('div', { class: 'whead' },
    h('button', { class: 'icon-btn back', type: 'button', 'aria-label': 'Назад', onclick: onBack }, icon('back', 24)), h('h1', null, title), extra || null);
  const body = (...kids) => h('div', { class: 'nbody' }, ...kids);

  /* ---------- выбор сценария ---------- */
  async function pick() {
    setBack(null); ss.set('neg:draft', null); ss.set('neg:text', null); sc = null; hist = []; finished = false; resultShown = false;
    root.replaceChildren(header('Переговоры', goBack), body(skeleton('card', 3)));
    try { if (!scenarios) scenarios = (await api.games()).scenarios; }
    catch (e) { root.replaceChildren(header('Переговоры', goBack), body(errorState(e, pick))); return; }
    const bests = best.get();
    root.replaceChildren(header('Переговоры', goBack), body(
      h('div', { class: 'page-head' }, h('h2', { class: 'h1' }, 'Выбери ситуацию'), h('p', { class: 'muted' }, 'ИИ сыграет собеседника. У тебя 6 реплик, чтобы договориться.')),
      h('div', { class: 'stack' }, scenarios.map((s) => h('button', { class: 'scard pressable', type: 'button', onclick: () => brief(s) },
        h('div', { class: 'row', style: { justifyContent: 'space-between', alignItems: 'flex-start' } }, h('h3', { class: 'h2' }, s.title),
          h('span', { class: 'chip static ' + (s.difficulty === 'легко' ? 'ok' : s.difficulty === 'средне' ? 'warn' : 'accent'), style: { flexShrink: 0 } }, s.difficulty)),
        h('p', { class: 'small muted' }, s.brief),
        h('div', { class: 'small', style: { fontWeight: 600 } }, 'Твоя роль: ', h('span', { style: { fontWeight: 500 } }, s.role)),
        bests[s.id] ? h('span', { class: 'chip static ok' }, `лучший результат ${bests[s.id]}/100`) : null)))));
  }

  function brief(s) {
    openSheet({ title: s.title, build: (close) => {
      const btn = h('button', { class: 'btn primary block', type: 'button' }, 'Начать переговоры');
      btn.addEventListener('click', async () => {
        btn.disabled = true; btn.replaceChildren(h('span', { class: 'spin' }));
        try {
          const r = await api.negStart(s.id);
          sc = r.scenario || s; max = r.max_turns || 6; mood = r.mood ?? 40; tip = null; finished = false; outcome = null; resultShown = false;
          hist = [{ role: 'them', text: r.opening }]; saveDraft(); close(); room();
        } catch (e) { haptic('error'); toast(e.friendly); btn.disabled = false; btn.replaceChildren('Начать переговоры'); }
      });
      const sec = (t, txt) => h('div', null, h('div', { class: 'blk-t', style: { fontSize: 'var(--t-xs)', fontWeight: 700, color: 'var(--ink-2)', textTransform: 'uppercase', letterSpacing: '.06em', marginBottom: '4px' } }, t), h('p', null, txt));
      return h('div', { class: 'stack-lg' },
        sec('Твоя роль', s.role), sec('Цель', s.goal), sec('Собеседник', 'Его характер и пределы не раскрыты — пойми их по ходу разговора.'),
        h('div', { class: 'chips' }, h('span', { class: 'chip static' }, `${max} ходов`), h('span', { class: 'chip static ' + (s.difficulty === 'легко' ? 'ok' : s.difficulty === 'средне' ? 'warn' : 'accent') }, s.difficulty)), btn);
    } });
  }

  /* ---------- комната ---------- */
  function room() {
    const leave = async () => { if (myTurns() > 0 && !finished && !(await confirmSheet({ title: 'Выйти из переговоров?', text: 'Прогресс этой попытки не сохранится.', confirm: 'Выйти', danger: true }))) return; pick(); };
    setBack(leave);
    const letter = (sc.title || 'С')[0];
    const list = h('div', { class: 'msgs nmsgs', role: 'log', 'aria-live': 'polite', 'aria-label': 'Переговоры' });
    const moodNum = h('b'), moodTxt = h('span'), delta = h('span', { class: 'delta', 'aria-hidden': 'true' }), fill = h('i');
    const track = h('div', { class: 'mtrack', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-label': 'Настрой собеседника' }, fill);
    const paintMood = (animDelta) => {
      moodNum.textContent = mood; moodTxt.textContent = moodWord(mood);
      track.setAttribute('aria-valuenow', mood); track.setAttribute('aria-valuetext', `${mood} из 100, ${moodWord(mood)}`);
      fill.style.background = moodColor(mood); requestAnimationFrame(() => { fill.style.width = mood + '%'; });
      if (animDelta) { delta.textContent = (animDelta > 0 ? '+' : '−') + Math.abs(animDelta); delta.className = 'delta show'; setTimeout(() => { delta.className = 'delta'; }, 1800); }
    };
    const moodBox = h('div', { class: 'mood' }, h('div', { class: 'row', style: { justifyContent: 'space-between' } }, h('span', { class: 'small', style: { fontWeight: 600 } }, 'Настрой собеседника'), h('span', { class: 'small' }, moodTxt, ' · ', moodNum, '/100 ', delta)), track);

    const bubble = (m) => m.role === 'me' ? h('div', { class: 'nb me' }, m.text)
      : h('div', { class: 'nb-row' }, h('span', { class: 'avatar', 'aria-hidden': 'true' }, letter), h('div', { class: 'nb them' }, m.text));
    const scroll = () => requestAnimationFrame(() => { const b = root.querySelector('.nbody'); if (b) b.scrollTo({ top: b.scrollHeight, behavior: 'smooth' }); });
    hist.forEach((m) => list.append(bubble(m)));

    const ta = h('textarea', { rows: 1, placeholder: 'Твоя реплика…', 'aria-label': 'Реплика', maxlength: 600 });
    ta.value = ss.get('neg:text') || '';
    const send = h('button', { class: 'send', type: 'submit', 'aria-label': 'Отправить', disabled: true }, icon('send', 22));
    const counter = h('span', { class: 'small muted' });
    const err = h('p', { class: 'err', role: 'alert', hidden: true });
    const sync = () => { send.disabled = busy || finished || !ta.value.trim(); ta.disabled = finished; ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 96) + 'px'; counter.textContent = finished ? 'переговоры завершены' : `ход ${Math.min(myTurns() + 1, max)} из ${max}`; ss.set('neg:text', ta.value || null); };
    ta.addEventListener('input', sync);
    ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); } });
    const form = h('form', { class: 'nfoot-form' }, h('div', { class: 'box' }, ta, send));
    const foot = h('div', { class: 'nfoot' }, h('div', { class: 'row', style: { justifyContent: 'space-between', padding: '0 4px 6px' } }, counter, err), form);

    const showTip = () => openSheet({ title: 'Подсказка', build: () => h('div', { class: 'stack' }, h('p', { class: 'sum', style: { fontWeight: 500, lineHeight: 1.5 } }, tip || TIPS[myTurns() % TIPS.length]), h('p', { class: 'hint' }, tip ? 'Совет коуча по последней реплике.' : 'Общий совет: он не зависит от хода разговора.')) });
    const tipBtn = h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Подсказка', onclick: showTip }, icon('bulb', 22));

    root.replaceChildren(header(sc.title, leave, tipBtn), moodBox, h('div', { class: 'nbody' }, list), foot);
    paintMood(0); sync(); scroll();

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const text = ta.value.trim(); if (!text || busy || finished) return;
      busy = true; err.hidden = true; const before = hist.slice();
      const mine = { role: 'me', text }; list.append(bubble(mine)); ta.value = ''; ss.set('neg:text', null); sync(); haptic('light');
      typingEl = h('div', { class: 'nb-row' }, h('span', { class: 'avatar', 'aria-hidden': 'true' }, letter), h('div', { class: 'typing', role: 'status' }, h('span', { class: 'dots', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')), h('span', null, 'Собеседник думает…')));
      list.append(typingEl); scroll();
      try {
        const r = await api.negTurn(sc.id, before, text);
        typingEl.remove(); typingEl = null;
        hist = [...before, mine, { role: 'them', text: r.reply }];
        const d = (r.mood ?? mood) - mood; mood = r.mood ?? mood; tip = r.tip || null; finished = !!r.finished; outcome = r.outcome || null;
        list.append(bubble({ role: 'them', text: r.reply })); paintMood(d); saveDraft(); haptic(d > 0 ? 'success' : 'light'); scroll();
      } catch (er) {
        typingEl?.remove(); typingEl = null; list.lastElementChild?.remove(); // убираем неотправленную реплику
        ta.value = text; err.textContent = er.friendly; err.hidden = false; haptic('error');
      } finally { busy = false; sync(); }
      if (finished) { await sleep(900); finish(); }
    });
    if (finished) finish();
    else if (!ta.disabled) setTimeout(() => ta.focus({ preventScroll: true }), 300);
  }

  /* ---------- итог ---------- */
  async function finish() {
    if (resultShown) return; resultShown = true;
    setBack(() => pick());
    root.replaceChildren(header('Результат', pick), body(h('div', { 'aria-live': 'polite', 'aria-busy': 'true' }, skeleton('card', 3), h('p', { class: 'hint', style: { textAlign: 'center', marginTop: '12px' } }, 'Разбираю переговоры…'))));
    let r;
    try { r = await api.negFinish(sc.id, hist); }
    catch (e) { resultShown = false; root.replaceChildren(header('Результат', pick), body(errorState(e, finish))); return; }
    ss.set('neg:draft', null); ss.set('neg:text', null);
    best.put(sc.id, r.score); haptic('success');
    const [oLabel, oCls, oIc] = OUT[r.outcome || outcome] || OUT.no_deal;
    const list = (items, ic, title) => items?.length ? h('div', { class: 'card tight stack' }, h('h3', { class: 'h2' }, title), h('ul', { class: 'stack small' }, items.map((t) => h('li', { class: 'row', style: { alignItems: 'flex-start' } }, h('span', { class: 'sico ' + (ic === 'check' ? 'ok' : 'risk'), 'aria-hidden': 'true' }, icon(ic, 14)), h('span', { class: 'grow', style: { fontSize: 'var(--t-md)' } }, t))))) : null;
    const share = async () => {
      const text = `Переговоры «${sc.title}»: ${r.score}/100 — ${oLabel.toLowerCase()}\nБизнес-навигатор`;
      try { if (navigator.share && matchMedia('(pointer: coarse)').matches) await navigator.share({ text }); else { await navigator.clipboard.writeText(text); toast('Результат скопирован'); } } catch (e) { /* отмена */ }
    };
    root.replaceChildren(header('Результат', pick), body(h('div', { class: 'stack-lg', 'aria-live': 'polite' },
      h('div', { class: 'card stack', style: { alignItems: 'center', textAlign: 'center' } },
        ring(r.score, 100, 112, 10, String(r.score)),
        h('span', { class: 'chip static ' + oCls }, icon(oIc, 14), oLabel),
        h('p', { class: 'sum', style: { fontWeight: 600, fontSize: 'var(--t-lg)', lineHeight: 1.4 } }, r.verdict)),
      list(r.strengths, 'check', 'Что получилось'), list(r.improvements, 'arrowr', 'Что улучшить'),
      r.better_phrases?.length ? h('div', { class: 'stack' }, h('h3', { class: 'h2' }, 'Лучше сказать так'), r.better_phrases.map((p) => h('div', { class: 'phrase' },
        h('div', { class: 'small muted' }, 'Было'), h('p', { class: 'was' }, p.instead), h('div', { class: 'small', style: { color: 'var(--ok)', fontWeight: 700 } }, 'Лучше'), h('p', { style: { fontWeight: 500 } }, p.say)))) : null,
      r.materials?.length ? h('div', { class: 'stack' }, h('h3', { class: 'h2' }, 'Что почитать'), r.materials.map((m) => materialCard(m))) : null,
      h('div', { class: 'stack' },
        h('button', { class: 'btn primary block', type: 'button', onclick: () => brief(sc) }, icon('refresh', 18), 'Ещё раз'),
        h('button', { class: 'btn soft block', type: 'button', onclick: pick }, 'Другой сценарий'),
        h('button', { class: 'btn text block', type: 'button', onclick: share }, icon('share', 18), 'Поделиться результатом')))));
    root.querySelector('.nbody').scrollTo(0, 0);
  }

  /* ---------- старт: продолжить черновик или выбрать ---------- */
  const d = ss.get('neg:draft');
  if (d?.sc && d.hist?.length) { ({ sc, hist, mood, max, tip, finished, outcome } = d); room(); } else pick();
  void go; void sleep;
  return () => { offBack(); typingEl = null; };
}
