// Онбординг: 5 вопросов полноэкранными шагами. Можно пропустить в любой момент.
import { h, icon, haptic } from '../ui.js';
import { api } from '../api.js';
import { state, options, FIELDS, showValue, setProfile } from '../state.js';
import { errorState } from '../components.js';

const STEPS = [
  { key: 'stage', q: 'На каком этапе твой бизнес?', hint: 'Это поможет подобрать материалы по уровню.' },
  { key: 'industry', q: 'В какой сфере ты работаешь?', grid: true },
  { key: 'goal', q: 'Что сейчас важнее всего?', hint: 'Выбери главное — остальное подтянем позже.' },
  { key: 'tax_regime', q: 'Какой у тебя налоговый режим?', hint: 'Не знаешь — так и ответь, разберёмся вместе.' },
  { key: 'employees', q: 'Есть ли у тебя сотрудники?', bool: true },
];
const LABEL = Object.fromEntries(FIELDS.map((f) => [f.key, f.label]));

export default function mount(view, { go }) {
  const answers = {};
  let i = -1;
  let saved = false;
  const root = h('div', { class: 'ob' });
  view.replaceChildren(root);

  const finishLater = async () => {
    // Сохраняем то, что успели ответить, и помечаем онбординг пройденным.
    if (saved) return;
    if (Object.keys(answers).length) setProfile(await api.saveProfile({ ...answers }));
    await api.onboardingDone();
    state.boot.onboarded = true; saved = true;
  };

  function top() {
    const pct = i < 0 ? 0 : Math.round(((i + 1) / STEPS.length) * 100);
    return h('div', { class: 'ob-top' },
      i >= 0 ? h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Назад', onclick: () => { i--; draw(); } }, icon('back', 24)) : h('span', { style: { width: '44px' } }),
      h('div', { class: 'pbar', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': STEPS.length, 'aria-valuenow': i + 1, 'aria-label': 'Прогресс' }, h('i', { style: { '--p': pct + '%' } })),
      h('button', { class: 'btn text sm', type: 'button', onclick: () => finish() }, 'Пропустить'));
  }

  function draw() {
    if (i < 0) return intro();
    if (i >= STEPS.length) return finish();
    const s = STEPS[i];
    const opts = s.bool ? [[true, 'Да, есть'], [false, 'Нет, работаю один']] : options(s.key === 'stage' ? 'stages' : s.key === 'industry' ? 'industries' : s.key === 'goal' ? 'goals' : 'tax_regimes').map((x) => [x, x]);
    const hints = FIELDS.find((f) => f.key === s.key)?.hints || {};
    const list = h('div', { class: 'choices' + (s.grid ? ' grid' : ''), role: 'radiogroup', 'aria-label': s.q },
      opts.map(([val, text]) => {
        const on = answers[s.key] === val;
        const b = h('button', { class: 'choice' + (on ? ' on' : ''), type: 'button', role: 'radio', 'aria-checked': String(on),
          onclick: () => {
            answers[s.key] = val; haptic('select');
            list.querySelectorAll('.choice').forEach((c) => { c.classList.remove('on'); c.setAttribute('aria-checked', 'false'); });
            b.classList.add('on'); b.setAttribute('aria-checked', 'true');
            setTimeout(() => { i++; draw(); }, 260);
          } },
          h('div', { class: 'grow' }, h('div', { class: 't' }, text[0].toUpperCase() + text.slice(1)), hints[text] ? h('div', { class: 's' }, hints[text]) : null),
          s.grid ? null : h('span', { class: 'tick' }, icon('check', 14)));
        return b;
      }));
    root.replaceChildren(top(),
      h('div', { class: 'ob-body' },
        h('div', { class: 'page-head', style: { margin: 0 } }, h('div', { class: 'eyebrow' }, `Вопрос ${i + 1} из ${STEPS.length}`), h('h1', { class: 'h1', tabindex: '-1' }, s.q), s.hint ? h('p', { class: 'muted' }, s.hint) : null),
        list));
    root.querySelector('h1').focus({ preventScroll: true });
  }

  function intro() {
    root.replaceChildren(
      h('div', { class: 'ob-top' }, h('span', { class: 'grow' }), h('button', { class: 'btn text sm', type: 'button', onclick: () => finish() }, 'Пропустить')),
      h('div', { class: 'ob-body', style: { justifyContent: 'center' } },
        h('div', { style: { width: '64px', height: '64px', borderRadius: '20px', background: 'var(--hero)', color: '#fff', display: 'grid', placeItems: 'center' } }, icon('route', 30)),
        h('div', { class: 'stack' },
          h('h1', { class: 'h1' }, 'Привет! Я ', h('span', { style: { whiteSpace: 'nowrap' } }, 'Бизнес-навигатор')),
          h('p', { class: 'muted' }, 'Отвечаю на вопросы по базе знаний, подбираю материалы, слежу за налоговыми сроками и напоминаю об учёбе. Пять быстрых вопросов — и советы станут точнее.')),
        h('button', { class: 'btn primary block', type: 'button', onclick: () => { i = 0; draw(); } }, 'Начать'),
        h('p', { class: 'hint' }, 'Займёт меньше минуты. Всё можно поменять в профиле.')));
  }

  async function finish() {
    i = STEPS.length;
    root.replaceChildren(h('div', { class: 'ob-body', style: { justifyContent: 'center', alignItems: 'center' }, 'aria-busy': 'true' }, h('div', { class: 'skel', style: { width: '120px', height: '14px' } })));
    try { await finishLater(); } catch (e) {
      root.replaceChildren(errorState(e, () => finish()), h('button', { class: 'btn text', type: 'button', onclick: () => go('today') }, 'Продолжить без сохранения'));
      return;
    }
    if (!state.boot.settings.weekly_summary_asked) {
      const choose = async (enabled) => {
        try {
          state.boot.settings = await api.saveSettings({ weekly_summary: enabled, weekly_summary_asked: true });
          finish();
        } catch (e) { root.replaceChildren(errorState(e, () => finish())); }
      };
      // Persist that the offer was shown; silence never means consent.
      try { state.boot.settings = await api.saveSettings({ weekly_summary_asked: true }); }
      catch (e) { root.replaceChildren(errorState(e, () => finish())); return; }
      root.replaceChildren(h('div', { class: 'ob-body' },
        h('h1', { class: 'h1' }, 'Получать итоги недели?'),
        h('p', { class: 'muted' }, `По воскресеньям в 19:00 (${state.boot.settings.timezone || 'по Москве, Europe/Moscow'}) отмечу твою активность в боте и отметки об обучении. Похвала за фактическую работу. Можно отключить в настройках.`),
        h('button', { class: 'btn primary block', type: 'button', onclick: () => choose(true) }, 'Да, получать итоги'),
        h('button', { class: 'btn text block', type: 'button', onclick: () => choose(false) }, 'Нет, спасибо')));
      return;
    }
    haptic('success');
    const f = state.boot.profile.fields;
    const rows = ['stage', 'industry', 'goal', 'tax_regime', 'employees'].map((k) => [LABEL[k], showValue(k, f[k])]).filter(([, v]) => v);
    root.replaceChildren(
      h('div', { class: 'ob-top' }),
      h('div', { class: 'ob-body' },
        h('div', { class: 'page-head', style: { margin: 0 } }, h('div', { class: 'eyebrow' }, 'Готово'), h('h1', { class: 'h1', tabindex: '-1' }, rows.length ? 'Вот что я запомнил' : 'Начнём без анкеты'),
          h('p', { class: 'muted' }, rows.length ? 'Это можно поправить в профиле. С чего начнём?' : 'Расскажешь о бизнесе по ходу разговора — я учту. С чего начнём?')),
        rows.length ? h('div', { class: 'card tight' }, rows.map(([k, v]) => h('div', { class: 'sumrow' }, h('span', null, k), h('span', null, v)))) : null,
        h('div', { class: 'stack', style: { marginTop: 'auto' } },
          h('button', { class: 'btn primary block', type: 'button', onclick: () => go('route') }, icon('route', 20), 'План на 7 дней'),
          h('button', { class: 'btn soft block', type: 'button', onclick: () => go('tools') }, icon('tools', 20), 'Инструменты'),
          h('button', { class: 'btn text block', type: 'button', onclick: () => go('today') }, 'Перейти на главную'))));
    root.querySelector('h1').focus({ preventScroll: true });
  }

  draw();
}
