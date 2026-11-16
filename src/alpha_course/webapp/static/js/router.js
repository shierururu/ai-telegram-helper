// Hash-роутер: #/today, #/ask, #/route?start=1 ...
import { h, icon, $, haptic } from './ui.js';
import { tg } from './tg.js';

const ROUTES = {
  today: { tab: 'today', load: () => import('./screens/today.js') },
  ask: { tab: 'ask', load: () => import('./screens/ask.js') },
  route: { tab: 'route', load: () => import('./screens/route.js') },
  taxes: { tab: 'more', sub: 'Налоги', parent: 'tools', load: () => import('./screens/taxes.js') },
  more: { tab: 'more', load: () => import('./screens/more.js') },
  reminders: { tab: 'more', sub: 'Напоминания', load: () => import('./screens/reminders.js') },
  profile: { tab: 'more', sub: 'Профиль', load: () => import('./screens/profile.js') },
  settings: { tab: 'more', sub: 'Настройки', load: () => import('./screens/settings.js') },
  tools: { tab: 'more', sub: 'Инструменты', parent: 'more', load: () => import('./screens/tools.js') },
  'tools/finmodel': { tab: 'more', sub: 'Финмодель', parent: 'tools', load: () => import('./screens/finmodel.js') },
  games: { tab: 'games', load: () => import('./screens/games.js') },
  'games/negotiation': { tab: 'games', full: true, parent: 'games', load: () => import('./screens/negotiation.js') },
  'games/myths': { tab: 'games', sub: 'Миф или правда', parent: 'games', load: () => import('./screens/myths.js') },
  'games/breakeven': { tab: 'games', sub: 'Точка безубыточности', parent: 'games', load: () => import('./screens/breakeven.js') },
  'games/idea': { tab: 'games', sub: 'Проверка идеи', parent: 'games', load: () => import('./screens/idea.js') },
  wordle: { tab: 'games', full: true, parent: 'games', load: () => import('./screens/wordle.js') },
  about: { tab: 'more', sub: 'О боте', load: () => import('./screens/more.js').then((m) => ({ default: m.about })) },
  onboarding: { bare: true, load: () => import('./screens/onboarding.js') },
};
export const TABS = [
  ['today', 'Сегодня', 'home'], ['ask', 'Спросить', 'chat'], ['route', 'Маршрут', 'route'], ['games', 'Игры', 'games'], ['more', 'Ещё', 'more'],
];

let token = 0;
let navCount = 0;
let parentOf = 'more';
const goBack = () => (navCount > 1 ? history.back() : go(parentOf));
let cleanup = null;

export function parse() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [name, q] = raw.split('?');
  return { name: ROUTES[name] ? name : 'today', query: new URLSearchParams(q || '') };
}
export function go(path, { replace = false } = {}) {
  const url = '#/' + path.replace(/^#?\/?/, '');
  if (replace) { history.replaceState(null, '', url); render(); } else if (location.hash === url) render(); else location.hash = url;
}

export const back = goBack;

export function mountChrome() {
  for (const a of document.querySelectorAll('.tab')) a.addEventListener('click', () => haptic('select'));
  window.addEventListener('hashchange', render);
}

export async function render() {
  const my = ++token; navCount++;
  const { name, query } = parse();
  const r = ROUTES[name];
  parentOf = r.parent || 'more';
  const view = $('#view'), top = $('#topbar');
  if (cleanup) { try { cleanup(); } catch (e) { /* noop */ } cleanup = null; }

  document.body.classList.toggle('bare', !!r.bare);
  document.body.classList.toggle('full', !!r.full);
  document.body.classList.add('ready');
  for (const a of document.querySelectorAll('.tab')) {
    if (a.dataset.tab === r.tab) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
  // верхняя панель только у вложенных экранов
  top.hidden = !r.sub;
  if (r.sub) {
    top.replaceChildren(
      h('button', { class: 'icon-btn back', type: 'button', 'aria-label': 'Назад', onclick: goBack }, icon('back', 24)),
      h('h1', null, r.sub));
  }
  tg.setBase(r.sub || r.full ? goBack : null);
  document.title = (r.sub ? r.sub + ' · ' : '') + 'Бизнес-навигатор';

  try {
    const mod = await r.load();
    if (my !== token) return;
    view.className = ''; view.replaceChildren(); void view.offsetWidth;
    view.className = r.full ? 'full' : 'enter';
    window.scrollTo(0, 0);
    const res = await mod.default(view, { query, go, name, back: goBack });
    if (my === token) cleanup = typeof res === 'function' ? res : null;
    else if (typeof res === 'function') res();
  } catch (e) {
    console.error(e);
    // Не загрузился файл экрана (нет сети, сервер или туннель недоступны): это не поломка кода.
    const network = e instanceof TypeError && /import|fetch|load failed|network/i.test(String(e.message));
    if (my === token) {
      view.replaceChildren(network
        ? h('div', { class: 'state', role: 'alert' }, h('h2', { class: 'h2' }, 'Нет связи с сервером'), h('p', null, 'Проверь интернет и обнови экран. Если не помогает, закрой мини-приложение и открой его заново из бота.'), h('button', { class: 'btn soft', type: 'button', onclick: () => location.reload() }, 'Обновить'))
        : h('div', { class: 'state', role: 'alert' }, h('h2', { class: 'h2' }, 'Что-то сломалось'), h('p', null, 'Обнови страницу или вернись на главную.'), h('a', { class: 'btn soft', href: '#/today' }, 'На главную')));
    }
  }
}
