// «Финмодель»: ввод цифр (или описание словами), расчёт на сервере, наглядный результат.
import { h, icon, haptic, toast } from '../ui.js';
import { api, getPdf } from '../api.js';
import { tg } from '../tg.js';
import { pageHead, segmented, materialCard, skeleton, errorState, section } from '../components.js';
import { rub, numSp } from '../fmt.js';

const KEY = 'fin:inputs';
const FIELDS = [
  ['price', 'Цена одной продажи', '₽', 'Средний чек: сколько платит клиент', true],
  ['unit_cost', 'Себестоимость одной продажи', '₽', 'Товар, материалы, упаковка на каждую продажу', false],
  ['units', 'Продаж в месяц', 'шт.', 'Сколько заказов или клиентов в месяц', true],
  ['fixed', 'Постоянные расходы', '₽/мес', 'Аренда, реклама, связь, софт', false],
  ['payroll', 'Зарплаты', '₽/мес', 'До налогов, взносы добавлю сам', false],
  ['investment', 'Стартовые вложения', '₽', 'Разовые траты на запуск — посчитаю окупаемость', false],
];
const SHORT = { npd: 'НПД', usn_income: 'УСН «Доходы»', usn_income_expense: 'УСН «Д−Р»' };
const VERDICT = { ok: ['Модель сходится', 'ok', 'check'], tight: ['Запас прочности небольшой', 'warn', 'alert'], loss: ['Работа в убыток', 'bad', 'alert'] };
const lsGet = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { return {}; } };
const lsSet = (v) => { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) { /* noop */ } };
const errText = (e) => (e.status === 0 ? e.friendly : (e.message || e.friendly));
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

export default async function mount(view) {
  const saved = lsGet();
  const st = { regime: saved.regime || null, client: saved.client || 'person' };
  const vals = Object.fromEntries(FIELDS.map(([k]) => [k, saved[k] ?? '']));
  const inputs = {};
  let meta = { regimes: [], calculators: [], profile_regime: null };
  let last = null, calcSeq = 0;

  const persist = () => lsSet({ ...vals, regime: st.regime, client: st.client });

  view.replaceChildren(pageHead('Инструменты', 'Финмодель', 'Прибыль, налог, точка безубыточности и окупаемость за минуту.'), h('div', { class: 'stack' }, skeleton('card', 2)));
  try { meta = await api.finmodelMeta(); } catch (e) { /* работаем с запасным списком */ }
  const regimes = meta.regimes?.length ? meta.regimes : [{ value: 'npd', label: 'НПД (самозанятый)' }, { value: 'usn_income', label: 'УСН «Доходы»' }, { value: 'usn_income_expense', label: 'УСН «Доходы минус расходы»' }];
  if (!regimes.some((r) => r.value === st.regime)) st.regime = (regimes.some((r) => r.value === meta.profile_regime) ? meta.profile_regime : regimes.find((r) => r.value === 'usn_income')?.value) || regimes[0].value;

  /* ---------- описание словами ---------- */
  const ta = h('textarea', { class: 'input', rows: 3, maxlength: 1200, placeholder: 'Например: кофейня, чашка 300 ₽, себестоимость 100, продаю около 500 в месяц, аренда 40 тысяч', 'aria-label': 'Описание бизнеса' });
  const exErr = h('p', { class: 'err', role: 'alert', hidden: true });
  const exBtn = h('button', { class: 'btn soft block', type: 'button', disabled: true }, icon('spark', 18), 'Заполнить по описанию');
  ta.addEventListener('input', () => { exBtn.disabled = ta.value.trim().length < 8; });
  exBtn.addEventListener('click', async () => {
    exErr.hidden = true; exBtn.disabled = true; exBtn.replaceChildren(h('span', { class: 'spin' }), 'Читаю описание…');
    try {
      const { fields = {} } = await api.finmodelExtract(ta.value.trim());
      let n = 0;
      for (const [k, v] of Object.entries(fields)) {
        if (k === 'client') { st.client = v; clientSeg?.set(v); n++; } else if (inputs[k]) { vals[k] = String(v); inputs[k].value = String(v); n++; }
      }
      persist(); drawNpd();
      if (n) { haptic('success'); toast(`Подставил ${n} значений, проверь`); } else toast('Не нашёл чисел в описании. Введи их вручную.');
    } catch (e) { haptic('error'); exErr.textContent = errText(e) + (e.status === 503 ? '' : ''); exErr.hidden = false; }
    finally { exBtn.disabled = ta.value.trim().length < 8; exBtn.replaceChildren(icon('spark', 18), 'Заполнить по описанию'); }
  });
  const aiCard = h('section', { class: 'card stack', 'aria-labelledby': 'fm-ai' },
    h('div', null, h('h2', { class: 'h2', id: 'fm-ai' }, 'Опиши бизнес своими словами'), h('p', { class: 'small muted' }, 'Достану числа из текста и подставлю в форму. Без имён и телефонов.')), ta, exErr, exBtn);

  /* ---------- форма ---------- */
  const fieldEls = FIELDS.map(([k, label, unit, hint, req]) => {
    const inp = h('input', { class: 'input', type: 'text', inputmode: 'decimal', autocomplete: 'off', enterkeyhint: 'next', id: 'fm-' + k, placeholder: '0', 'aria-describedby': 'fm-h-' + k });
    inp.value = vals[k];
    inp.addEventListener('input', () => { vals[k] = inp.value; inp.classList.remove('bad'); persist(); });
    inputs[k] = inp;
    return h('div', { class: 'field' },
      h('label', { for: 'fm-' + k }, label, req ? h('span', { class: 'req', 'aria-hidden': 'true' }, ' *') : h('span', { class: 'opt' }, ' · необязательно')),
      h('div', { class: 'unit' }, inp, h('span', { 'aria-hidden': 'true' }, unit)),
      h('div', { class: 'small muted', id: 'fm-h-' + k }, hint));
  });

  const regimeHint = h('p', { class: 'small muted' });
  const drawRegime = () => { regimeHint.textContent = regimes.find((r) => r.value === st.regime)?.label || ''; };
  const regSeg = segmented(regimes.map((r) => [r.value, SHORT[r.value] || r.label]), st.regime, (v) => { st.regime = v; drawRegime(); drawNpd(); persist(); }, 'Налоговый режим');
  const npdHost = h('div', { class: 'field' });
  let clientSeg = null;
  function drawNpd() {
    if (st.regime !== 'npd') { npdHost.replaceChildren(); npdHost.hidden = true; return; }
    npdHost.hidden = false;
    clientSeg = segmented([['person', 'Физлица · 4%'], ['company', 'Компании и ИП · 6%']], st.client, (v) => { st.client = v; persist(); }, 'Кто ваши клиенты');
    npdHost.replaceChildren(h('label', null, 'Кто ваши клиенты'), clientSeg);
  }
  drawRegime(); drawNpd();

  const formErr = h('p', { class: 'err', role: 'alert', hidden: true });
  const calcBtn = h('button', { class: 'btn primary block', type: 'button' }, icon('calc', 18), 'Посчитать');
  const resHost = h('div', { class: 'fm-res', 'aria-live': 'polite' });

  const form = h('section', { class: 'card stack', 'aria-labelledby': 'fm-f' },
    h('h2', { class: 'h2', id: 'fm-f' }, 'Цифры бизнеса'), fieldEls,
    h('div', { class: 'field' }, h('label', null, 'Налоговый режим'), regSeg, regimeHint), npdHost, formErr, calcBtn,
    h('button', { class: 'btn text', type: 'button', style: { alignSelf: 'center' }, onclick: () => {
      for (const [k] of FIELDS) { vals[k] = ''; inputs[k].value = ''; inputs[k].classList.remove('bad'); }
      persist(); formErr.hidden = true; last = null; calcSeq++; resHost.replaceChildren(); resHost.classList.remove('stale'); toast('Поля очищены');
    } }, 'Очистить поля'));

  view.replaceChildren(h('div', { class: 'stack-lg tool' }, pageHead('Инструменты', 'Финмодель', 'Прибыль, налог, точка безубыточности и окупаемость за минуту.'), aiCard, form, resHost));

  /* ---------- расчёт ---------- */
  calcBtn.addEventListener('click', async () => {
    formErr.hidden = true;
    const miss = FIELDS.filter(([k, , , , req]) => req && !String(vals[k]).trim());
    FIELDS.forEach(([k]) => inputs[k].classList.toggle('bad', miss.some((m) => m[0] === k)));
    if (miss.length) { formErr.textContent = 'Заполни: ' + miss.map((m) => m[1].toLowerCase()).join(', ') + '.'; formErr.hidden = false; last = null; resHost.replaceChildren(); resHost.classList.remove('stale'); inputs[miss[0][0]].focus(); haptic('error'); return; }
    const body = { regime: st.regime };
    for (const [k] of FIELDS) if (String(vals[k]).trim()) body[k] = String(vals[k]).trim();
    if (st.regime === 'npd') body.client = st.client;
    const seq = ++calcSeq;
    calcBtn.disabled = true; calcBtn.replaceChildren(h('span', { class: 'spin' }), 'Считаю…');
    if (last) resHost.classList.add('stale'); else resHost.replaceChildren(skeleton('card', 2));
    try {
      const r = await api.finmodelCalc(body);
      if (seq !== calcSeq) return;
      last = r; haptic('success'); drawResult(r, body);
      resHost.classList.remove('stale');
      if (!reduced()) resHost.firstElementChild?.scrollIntoView({ behavior: 'smooth', block: 'start' }); else resHost.firstElementChild?.scrollIntoView({ block: 'start' });
    } catch (e) {
      if (seq !== calcSeq) return;
      haptic('error'); resHost.classList.remove('stale');
      if (!last) resHost.replaceChildren();
      formErr.textContent = errText(e); formErr.hidden = false;
    } finally { if (seq === calcSeq) { calcBtn.disabled = false; calcBtn.replaceChildren(icon('calc', 18), 'Посчитать'); } }
  });

  /* ---------- результат ---------- */
  function drawResult(r, body) {
    const [vText, vCls, vIco] = VERDICT[r.verdict] || VERDICT.ok;
    const invest = Number(String(body.investment || '0').replace(/\s/g, '').replace(',', '.')) || 0;
    const stat = (label, value, sub) => h('div', { class: 'fm-stat' }, h('div', { class: 'small muted' }, label), h('b', null, value), sub ? h('div', { class: 'small muted' }, sub) : null);
    const expenses = r.variable + r.fixed_total;

    const hero = h('div', { class: `card stack fm-hero v-${vCls}`, tabindex: '-1' },
      h('span', { class: `chip static ${vCls === 'bad' ? 'accent' : vCls}`, style: { alignSelf: 'flex-start' } }, icon(vIco, 14), vText),
      h('div', null, h('div', { class: 'small muted' }, 'Чистая прибыль в месяц'), h('div', { class: 'fm-big' }, rub(r.profit))),
      h('p', { class: 'small muted' }, `${r.regime_label}, налог ${numSp(r.tax_rate)}%`));

    const stats = h('div', { class: 'fm-stats' },
      stat('Выручка', rub(r.revenue)), stat('Расходы', rub(expenses), 'без налогов'),
      stat('Налоги', rub(r.tax), `ставка ${numSp(r.tax_rate)}%`), stat('Маржа', `${numSp(r.margin_pct)}%`, `${rub(r.unit_margin)} с продажи`));

    const planned = Number(String(body.units).replace(/\s/g, '').replace(',', '.')) || 0;
    const even = r.break_even_units;
    let be;
    if (even == null) be = h('p', { class: 'small' }, 'Точка безубыточности недостижима: цена не выше затрат на одну продажу.');
    else if (even === 0) be = h('p', { class: 'small' }, 'Бизнес в плюсе с первой продажи: постоянные расходы покрываются без выручки.');
    else {
      const scale = Math.max(planned, even) * 1.12 || 1;
      be = h('div', { class: 'stack', style: { gap: '10px' } },
        h('div', { class: 'row', style: { gap: 'var(--s-4)' } },
          h('div', { class: 'grow' }, h('div', { class: 'small muted' }, 'Нужно продаж'), h('b', { class: 'fm-num' }, `${numSp(even)} шт.`)),
          h('div', { class: 'grow' }, h('div', { class: 'small muted' }, 'Нужна выручка'), h('b', { class: 'fm-num' }, rub(r.break_even_revenue)))),
        h('div', { class: 'fm-bar', role: 'img', 'aria-label': `План ${numSp(planned)} продаж, безубыточность ${numSp(even)}` },
          h('i', { class: `v-${vCls}`, style: { width: Math.min(100, planned / scale * 100) + '%' } }), h('u', { style: { left: even / scale * 100 + '%' } })),
        h('div', { class: 'row small', style: { justifyContent: 'space-between' } }, h('span', null, `План: ${numSp(planned)}`), h('span', { class: 'muted' }, `Безубыточность: ${numSp(even)}`)),
        r.safety_pct != null ? h('p', { class: 'small' }, r.safety_pct >= 0 ? `Запас прочности ${numSp(r.safety_pct)}%: продажи могут упасть на столько, и вы всё равно не уйдёте в минус.` : `Не хватает ${numSp(Math.abs(r.safety_pct))}% до нуля.`) : null);
    }
    const payback = r.payback_months != null ? `${numSp(r.payback_months)} мес.` : invest > 0 ? 'не окупается' : 'вложений нет';
    const beCard = h('section', { class: 'card stack', 'aria-labelledby': 'fm-be' }, h('h2', { class: 'h2', id: 'fm-be' }, 'Точка безубыточности'), be,
      h('hr', { class: 'divider' }), h('div', { class: 'row', style: { justifyContent: 'space-between' } }, h('span', { class: 'muted' }, 'Окупаемость вложений'), h('b', { class: 'fm-num' }, payback)));

    const scen = h('section', { class: 'stack' }, section('Что если'), h('div', { class: 'list' }, r.scenarios.map((s) => h('div', { class: 'item fm-row' },
      h('div', { class: 'grow' }, h('div', { class: 't' }, s.label), h('div', { class: 's' }, `${numSp(s.units)} шт. · выручка ${rub(s.revenue)}`)),
      h('b', { class: 'fm-num ' + (s.profit < 0 ? 'neg' : 'pos') }, rub(s.profit, { sign: true }))))));

    const reg = h('section', { class: 'stack' }, section('Сравнение налоговых режимов'), h('div', { class: 'list' }, r.regimes.map((x) => h('div', { class: 'item fm-row' + (x.available ? '' : ' off') },
      h('div', { class: 'grow' }, h('div', { class: 't' }, x.label),
        h('div', { class: 's' }, x.available ? `налог ${rub(x.tax)}` : 'недоступен при таких данных'),
        (x.best || x.current) ? h('div', { class: 'chips', style: { marginTop: '6px' } }, x.best ? h('span', { class: 'chip static ok' }, icon('check', 12), 'выгоднее всего') : null, x.current ? h('span', { class: 'chip static' }, 'ваш выбор') : null) : null),
      h('b', { class: 'fm-num ' + (x.profit < 0 ? 'neg' : 'pos') }, x.available ? rub(x.profit, { sign: true }) : '—')))));

    const warns = (r.warnings || []).map((w) => h('div', { class: 'note' }, icon('alert', 18), h('span', null, w)));
    const calcs = meta.calculators?.length ? h('section', { class: 'stack' }, section('Калькуляторы из базы знаний'), meta.calculators.map((m) => materialCard(m))) : null;

    const copyBtn = h('button', { class: 'btn soft block', type: 'button', onclick: () => copy(summary(r, body, expenses, payback), 'Итог скопирован') }, icon('copy', 18), 'Копировать итог');

    const pdfBtn = h('button', { class: 'btn primary block', type: 'button', onclick: async (e) => {
      const btn = e.currentTarget, label = btn.lastChild.textContent;
      btn.disabled = true; btn.lastChild.textContent = 'Собираю PDF…';
      try { const res = await getPdf('/api/finmodel/pdf', body, 'finmodel.pdf'); haptic('success'); toast(res.sent ? 'Отправил PDF в чат с ботом' : res.mock ? 'PDF готов (демо-режим)' : 'PDF сохранён'); }
      catch (er) { haptic('error'); toast(er.friendly); }
      finally { btn.disabled = false; btn.lastChild.textContent = label; }
    } }, icon('download', 18), tg.inTelegram ? 'Получить PDF в чате' : 'Скачать PDF');

    resHost.replaceChildren(hero, stats, ...warns, beCard, scen, reg, calcs, pdfBtn, copyBtn,
      h('p', { class: 'hint' }, 'Ставки по умолчанию, в вашем регионе могут отличаться.'));
  }

  function summary(r, body, expenses, payback) {
    const lines = [`Финмодель (${r.regime_label})`, `Выручка: ${rub(r.revenue)}`, `Расходы: ${rub(expenses)}`, `Налоги: ${rub(r.tax)} (${numSp(r.tax_rate)}%)`, `Прибыль в месяц: ${rub(r.profit)}`, `Маржа: ${numSp(r.margin_pct)}%`];
    if (r.break_even_units != null) lines.push(`Безубыточность: ${numSp(r.break_even_units)} продаж, ${rub(r.break_even_revenue)}`);
    lines.push(`Окупаемость: ${payback}`, 'Ставки по умолчанию, в вашем регионе могут отличаться.');
    return lines.join('\n');
  }
  async function copy(text, ok) {
    try {
      if (navigator.share && matchMedia('(pointer:coarse)').matches) await navigator.share({ text });
      else { await navigator.clipboard.writeText(text); toast(ok); }
    } catch (e) {
      if (e?.name === 'AbortError') return;
      try { const t = h('textarea', { style: { position: 'fixed', opacity: 0 } }); t.value = text; document.body.append(t); t.select(); document.execCommand('copy'); t.remove(); toast(ok); } catch (er) { toast('Не получилось скопировать'); }
    }
  }
}
