// «Ещё» и «О боте».
import { h, icon } from '../ui.js';
import { tg } from '../tg.js';
import { state, completeness } from '../state.js';
import { streakNow } from '../wordle-store.js';
import { MODE } from '../api.js';

export default function mount(view) {
  const { n, total } = completeness();
  const rems = state.boot.reminders?.length || 0;
  const row = (href, ic, title, sub, right) => h('a', { class: 'item', href },
    h('div', { class: 'ico' }, icon(ic, 20)), h('div', { class: 'grow' }, h('div', { class: 't' }, title), sub ? h('div', { class: 's' }, sub) : null),
    right || null, h('span', { class: 'chev' }, icon('chev', 20)));
  view.replaceChildren(
    h('div', { class: 'page-head' }, h('div', { class: 'eyebrow' }, 'Бизнес-навигатор'), h('h1', { class: 'h1' }, 'Ещё')),
    h('div', { class: 'list' },
      row('#/tools', 'tools', 'Инструменты', 'Финмодель, игры и налоговый календарь'),
      row('#/wordle', 'word', 'Слово дня', 'Ежедневная игра с терминами', streakNow() > 0 ? h('span', { class: 'streak' }, icon('flame', 14), streakNow()) : null),
      row('#/reminders', 'bell', 'Напоминания', rems ? `Активных: ${rems}` : 'Об учёбе и важных делах', rems ? h('span', { class: 'badge' }, rems) : null),
      row('#/profile', 'user', 'Профиль бизнеса', `Заполнено ${n} из ${total}`),
      row('#/settings', 'sliders', 'Настройки', 'Город, итоги недели и уведомления'),
      row('#/about', 'info', 'О боте', 'Что умею и как хранятся данные')));
}

export function about(view) {
  const li = (ic, t, s) => h('li', { class: 'row', style: { alignItems: 'flex-start' } }, h('div', { class: 'item' , style: { padding: 0, minHeight: 0, alignItems: 'flex-start' } }, h('div', { class: 'ico' }, icon(ic, 20)), h('div', { class: 'grow' }, h('div', { class: 't' }, t), h('div', { class: 's' }, s))));
  view.replaceChildren(
    h('div', { class: 'stack-lg' },
      h('div', { class: 'hero' }, h('h2', { class: 'h2', style: { fontSize: 'var(--t-xl)' } }, 'Бизнес-навигатор'), h('p', { class: 'muted' }, 'Помощник для предпринимателей: отвечает по базе знаний, подбирает материалы и мягко напоминает об учёбе и важных сроках.')),
      h('div', { class: 'card' }, h('ul', { class: 'stack-lg' },
        li('chat', 'Консультация', 'Отвечаю по подготовленной базе. Если в ней нет ответа — так и скажу, без выдумок.'),
        li('route', 'План на 7 дней', 'Шаги подбираются под профиль. План запускается только с твоего согласия.'),
        li('calendar', 'Налоговый календарь', 'Показываю только сроки, которые можно рассчитать по профилю.'),
        li('bell', 'Напоминания', 'Разовые и повторяющиеся. Частоту можно менять, а уведомления — отключить в любой момент.'),
        li('shield', 'Данные', 'Профиль хранится только для персонализации. Его можно изменить или удалить в разделе «Профиль».'))),
      h('p', { class: 'hint' }, 'Платные материалы показываю кратко — подробности на странице материала.' + (MODE !== 'prod' ? ` Режим: ${MODE}.` : '')),
      tg.inTelegram ? h('button', { class: 'btn soft block', type: 'button', onclick: () => tg.close() }, 'Вернуться в чат') : null));
}
