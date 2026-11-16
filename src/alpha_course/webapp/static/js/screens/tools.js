// «Инструменты»: финмодель, тренажёры и налоговый календарь.
import { h, icon } from '../ui.js';
import { api } from '../api.js';
import { pageHead, skeleton, errorState } from '../components.js';

const ART = {
  finmodel: '<rect x="12" y="52" width="16" height="30" rx="5" class="a-line"/><rect x="40" y="36" width="16" height="46" rx="5" class="a-mid"/><rect x="68" y="16" width="16" height="66" rx="5" class="a-ok"/><path d="M14 40l24-14 18 8 26-22" fill="none" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round" class="a-stroke"/>',
};
const META = {
  finmodel: { href: '#/tools/finmodel', chips: ['3 налоговых режима', 'PDF-отчёт'] },
};
const art = (k) => { const s = h('svg', { viewBox: '0 0 96 96', width: 76, height: 76, 'aria-hidden': 'true', focusable: 'false' }); s.innerHTML = ART[k]; return s; };

export default async function mount(view) {
  const host = h('div', { class: 'stack' }, skeleton('card', 2));
  view.replaceChildren(pageHead('Инструменты', 'Рабочие инструменты', 'Посчитай экономику идеи, потренируйся и проверь налоговые сроки.'), host);
  const shortcuts = () => [
    ['#/games', 'games', 'Игры и тренажёры', 'Слово дня, мифы о бизнесе, безубыточность и переговоры'],
    ['#/taxes', 'calendar', 'Налоговый календарь', 'Сроки и платежи по твоему профилю'],
  ].map(([href, ic, title, sub]) => h('a', { class: 'card tight row pressable', href },
    h('div', { class: 'datebox' }, icon(ic, 24)),
    h('div', { class: 'grow' }, h('h2', { class: 'h2' }, title), h('p', { class: 'small muted' }, sub)),
    h('span', { class: 'chev muted' }, icon('chev', 20))));
  const draw = async () => {
    host.replaceChildren(skeleton('card', 2));
    try {
      const { tools } = await api.tools();
      host.replaceChildren(...tools.filter((t) => META[t.id]).map((t) => h('a', { class: `gcard pressable g-${t.id}`, href: META[t.id].href },
        h('div', { class: 'gart' }, art(t.id)),
        h('div', { class: 'grow stack', style: { gap: '6px' } }, h('h2', { class: 'h2' }, t.title), h('p', { class: 'small muted' }, t.tagline),
          h('div', { class: 'chips' }, META[t.id].chips.map((c) => h('span', { class: 'chip static' }, c)))),
        h('span', { class: 'chev muted' }, icon('chev', 20)))), ...shortcuts());
    } catch (e) { host.replaceChildren(errorState(e, draw), ...shortcuts()); }
  };
  await draw();
}
