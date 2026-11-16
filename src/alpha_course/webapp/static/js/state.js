// Общее состояние приложения в памяти.
import { api } from './api.js';

export const state = {
  boot: null,
  chat: [],            // сообщения консультации
  pendingQuestion: null,
};

const FALLBACK = {
  stages: ['идея', 'запуск', 'работает до года', 'развитие'],
  industries: ['розница', 'общепит', 'услуги', 'онлайн-торговля и маркетплейсы', 'производство', 'строительство', 'логистика', 'IT и digital', 'образование', 'красота и здоровье', 'оптовая торговля', 'другое'],
  legal_forms: ['самозанятый', 'ИП', 'ООО', 'пока не зарегистрирован'],
  tax_regimes: ['НПД', 'УСН «Доходы»', 'УСН «Доходы минус расходы»', 'ПСН', 'АУСН', 'ОСНО', 'не знаю'],
  goals: ['найти клиентов', 'разобраться с налогами', 'запустить дело', 'снизить расходы', 'увеличить продажи', 'собрать команду'],
};

export const FIELDS = [
  { key: 'stage', label: 'Этап бизнеса', opts: 'stages', hints: { 'идея': 'Пока проверяю идею', 'запуск': 'Только открываюсь', 'работает до года': 'Бизнес идёт меньше года', 'развитие': 'Расту и масштабируюсь' } },
  { key: 'industry', label: 'Сфера', opts: 'industries' },
  { key: 'goal', label: 'Главная цель', opts: 'goals' },
  { key: 'legal_form', label: 'Форма бизнеса', opts: 'legal_forms' },
  { key: 'tax_regime', label: 'Налоговый режим', opts: 'tax_regimes', hints: { 'не знаю': 'Помогу разобраться' } },
  { key: 'employees', label: 'Сотрудники', bool: true },
  { key: 'level', label: 'Опыт', levels: true },
];
export const LEVELS = { novice: 'новичок', experienced: 'есть опыт' };

export function options(key) {
  const o = state.boot?.options?.[key];
  return o && o.length ? o : FALLBACK[key] || [];
}

export function showValue(key, v) {
  if (v === null || v === undefined || v === '') return null;
  if (key === 'employees') return v ? 'есть' : 'нет';
  if (key === 'level') return LEVELS[v] || v;
  return v;
}

export function fields() { return state.boot?.profile?.fields || {}; }
const COUNTED = ['stage', 'industry', 'goal', 'legal_form', 'tax_regime', 'employees'];
export function completeness() {
  const f = fields();
  const n = COUNTED.filter((k) => f[k] !== null && f[k] !== undefined && f[k] !== '').length;
  return { n, total: COUNTED.length };
}

export async function loadBoot(force = false) {
  if (state.boot && !force) return state.boot;
  const b = await api.bootstrap();
  state.boot = b;
  return b;
}
export function setProfile(p) { if (state.boot && p) { state.boot.profile = p; } }

export async function refreshReminders() {
  const r = await api.reminders();
  if (state.boot) state.boot.reminders = r.reminders || [];
  return state.boot?.reminders || [];
}
