// «Проверка идеи»: оценка бизнес-идеи по базе знаний.
import { h, icon, haptic, toast } from '../ui.js';
import { api } from '../api.js';
import { ring, errorState, materialCard } from '../components.js';
import { openReminderComposer } from '../reminder-flow.js';

const EXAMPLES = ['Кофейня у дома', 'Онлайн-школа английского', 'Доставка домашней еды'];
const V = { go: ['Можно пробовать', 'ok', 'check'], test: ['Нужна проверка', 'warn', 'help'], rethink: ['Стоит пересмотреть', 'accent', 'alert'] };
const ST = { ok: ['В порядке', 'ok', 'check'], risk: ['Есть риск', 'risk', 'alert'], unknown: ['Не ясно', 'unk', 'help'] };
const LS = { get: (k) => { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* noop */ } } };
const STAGES = ['Ищу в базе знаний…', 'Сверяю с чек-листом…', 'Собираю выводы…'];
const NEED = { documents: 'Документы', taxes: 'Налоги', permits: 'Разрешения' };

export default function mount(view, { go }) {
  const host = h('div', { class: 'stack-lg' });
  view.replaceChildren(host);
  let st = LS.get('idea:last') || null; // {idea, city, answers, result}
  let timer = null;

  /* ---------- ввод ---------- */
  function form(prefill = {}) {
    const ta = h('textarea', { class: 'input', rows: 5, maxlength: 600, placeholder: 'Что ты хочешь продавать, кому и как?', 'aria-label': 'Описание идеи' });
    ta.value = prefill.idea || '';
    const count = h('span', { class: 'small muted', 'aria-live': 'off' });
    const city = h('input', { class: 'input', placeholder: 'Город (необязательно)', 'aria-label': 'Город', autocomplete: 'address-level2' });
    city.value = prefill.city || '';
    const err = h('p', { class: 'err', role: 'alert', hidden: true });
    const btn = h('button', { class: 'btn primary block', type: 'button' }, icon('target', 18), 'Проверить идею');
    const sync = () => { count.textContent = `${ta.value.length}/600`; btn.disabled = ta.value.trim().length < 8; };
    ta.addEventListener('input', sync); sync();
    btn.addEventListener('click', () => run({ idea: ta.value.trim(), city: city.value.trim() || undefined, answers: [] }, null, err));
    host.replaceChildren(
      h('div', { class: 'page-head', style: { margin: 0 } }, h('p', { class: 'muted' }, 'Опиши идею своими словами. Сверю её с базой знаний: что нужно оформить, где риски и что стоит уточнить.')),
      h('div', { class: 'chips' }, EXAMPLES.map((x) => h('button', { class: 'chip', type: 'button', onclick: () => { ta.value = x + ': '; sync(); ta.focus(); } }, x))),
      h('div', { class: 'stack' }, h('div', { class: 'field' }, h('label', null, 'Твоя идея'), ta, h('div', { style: { textAlign: 'right' } }, count)), h('div', { class: 'field' }, h('label', null, 'Город'), city), err, btn),
      h('p', { class: 'hint' }, 'Не пиши личные данные: имена, телефоны, адреса.'));
  }

  /* ---------- запрос ---------- */
  async function run(payload, prev, errEl) {
    let i = 0;
    const label = h('p', { class: 'sum', style: { fontWeight: 600 } }, STAGES[0]);
    host.replaceChildren(h('div', { class: 'card stack', 'aria-busy': 'true' }, h('div', { class: 'skel', style: { height: '90px', width: '90px', borderRadius: '50%', alignSelf: 'center' } }),
      h('div', { 'aria-live': 'polite', style: { textAlign: 'center' } }, label), h('div', { class: 'skel', style: { height: '14px', width: '85%' } }), h('div', { class: 'skel', style: { height: '14px', width: '60%' } })));
    timer = setInterval(() => { i = Math.min(i + 1, STAGES.length - 1); label.textContent = STAGES[i]; }, 1400);
    try {
      const r = await api.ideaCheck({ idea: payload.idea, city: payload.city, answers: payload.answers?.length ? payload.answers : undefined });
      clearInterval(timer);
      st = { idea: payload.idea, city: payload.city, answers: payload.answers || [], result: r };
      LS.set('idea:last', st);
      if (!prev) { try { localStorage.setItem('idea:count', String((+localStorage.getItem('idea:count') || 0) + 1)); } catch (e) { /* noop */ } }
      haptic('success'); result(r, prev);
    } catch (e) {
      clearInterval(timer); haptic('error');
      if (prev) { result(prev.result, prev, e); return; }
      form(payload);
      const m = host.querySelector('.err'); if (m) { m.textContent = e.friendly; m.hidden = false; }
    }
  }

  /* ---------- результат ---------- */
  function result(r, prevState, error) {
    const [vl, vcls, vic] = V[r.verdict] || V.test;
    const prevScore = prevState && prevState.result !== r ? prevState.result?.score : null;
    const inputs = [];
    const refine = h('button', { class: 'btn primary block', type: 'button', disabled: true }, 'Уточнить оценку');
    const errBox = h('p', { class: 'err', role: 'alert', hidden: !error }, error ? error.friendly : '');
    refine.addEventListener('click', () => {
      const answers = [...(st.answers || []).filter((a) => !inputs.some((x) => x.q === a.question)), ...inputs.filter((x) => x.el.value.trim()).map((x) => ({ question: x.q, answer: x.el.value.trim() }))];
      run({ idea: st.idea, city: st.city, answers }, { ...st, result: r }, errBox);
    });
    const syncRefine = () => { refine.disabled = !inputs.some((x) => x.el.value.trim()); };

    const group = (k) => r.needs?.[k]?.length ? h('div', { class: 'stack', style: { gap: '6px' } }, h('div', { class: 'label' }, NEED[k]), h('div', { class: 'chips' }, r.needs[k].map((t) => h('span', { class: 'chip static' }, t)))) : null;
    const needs = ['documents', 'taxes', 'permits'].map(group).filter(Boolean);

    host.replaceChildren(...[
      h('div', { class: 'card stack', style: { alignItems: 'center', textAlign: 'center' }, 'aria-live': 'polite' },
        ring(r.score, 100, 104, 9, String(r.score)),
        h('span', { class: 'chip static ' + vcls }, icon(vic, 14), vl),
        prevScore != null ? h('span', { class: 'chip static ok' }, `Оценка обновлена: ${prevScore} → ${r.score}`) : null,
        h('p', { style: { fontWeight: 500, lineHeight: 1.5 } }, r.summary)),
      !r.grounded ? h('div', { class: 'note' }, icon('info', 18), h('span', null, 'Ответ опирается на ограниченные данные базы знаний: часть выводов — предположения, перепроверь их.')) : null,
      h('div', { class: 'note' }, icon('info', 18), h('span', null, 'Рынок и конкурентов в базе нет — проверь отдельно.')),
      r.checklist?.length ? h('div', { class: 'stack' }, h('h2', { class: 'h2' }, 'Чек-лист'), h('div', { class: 'list' }, r.checklist.map((c) => {
        const [sl, scls, sic] = ST[c.status] || ST.unknown;
        return h('details', { class: 'crow' }, h('summary', null, h('span', { class: 'sico ' + scls, 'aria-hidden': 'true' }, icon(sic, 14)),
          h('span', { class: 'grow' }, h('span', { class: 't' }, c.title), h('span', { class: 'small muted', style: { display: 'block' } }, sl)), h('span', { class: 'chev' }, icon('chev', 18))),
          h('div', { class: 'cbody stack' }, h('p', { class: 'small' }, h('b', null, 'Почему: '), c.why), c.material ? materialCard(c.material) : null));
      }))) : null,
      needs.length ? h('div', { class: 'card stack' }, h('h2', { class: 'h2' }, 'Что нужно оформить'), needs) : null,
      r.risks?.length ? h('div', { class: 'card stack' }, h('h2', { class: 'h2' }, 'Риски'), h('ul', { class: 'stack' }, r.risks.map((t) => h('li', { class: 'row', style: { alignItems: 'flex-start' } }, h('span', { class: 'sico risk', 'aria-hidden': 'true' }, icon('alert', 14)), h('span', { class: 'grow' }, t))))) : null,
      r.questions?.length ? h('div', { class: 'card stack' }, h('div', null, h('h2', { class: 'h2' }, 'Что уточнить'), h('p', { class: 'small muted' }, 'Ответь на то, что знаешь, — оценка станет точнее.')),
        r.questions.map((q) => { const el = h('textarea', { class: 'input', rows: 2, maxlength: 300, 'aria-label': q, placeholder: 'Твой ответ' }); inputs.push({ q, el }); el.addEventListener('input', syncRefine); return h('div', { class: 'field' }, h('label', null, q), el); }),
        errBox, refine) : (error ? errBox : null),
      r.next_steps?.length ? h('div', { class: 'card stack' }, h('h2', { class: 'h2' }, 'Следующие шаги'), h('ol', { class: 'numl' }, r.next_steps.map((t) => h('li', null, t)))) : null,
      r.materials?.length ? h('div', { class: 'stack' }, h('h2', { class: 'h2' }, 'Что почитать'), r.materials.map((m) => materialCard(m))) : null,
      h('div', { class: 'stack' },
        h('button', { class: 'btn primary block', type: 'button', onclick: () => go('route?start=1&goal=' + encodeURIComponent('Запустить: ' + st.idea.slice(0, 80))) }, icon('target', 18), 'План на 7 дней под эту идею'),
        r.next_steps?.length ? h('button', { class: 'btn soft block', type: 'button', onclick: () => openReminderComposer({ event: 'Шаг по идее: ' + r.next_steps[0] }) }, icon('bell', 18), 'Напомнить про шаг') : null,
        h('button', { class: 'btn text block', type: 'button', onclick: () => { st = null; form(); } }, 'Проверить другую идею'))].filter(Boolean));
  }

  if (st?.result) result(st.result, null); else form();
  return () => clearInterval(timer);
}
