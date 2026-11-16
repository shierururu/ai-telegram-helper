// Форматирование дат и подписей на русском.
const MONTHS_G = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const MONTHS_S = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const WD = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];
const WD_S = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];

export const monthShort = (d) => MONTHS_S[d.getMonth()];
export const weekdayShort = (d) => WD_S[d.getDay()];
export const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

export const pad = (n) => String(n).padStart(2, '0');
export const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const parseYmd = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
export const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());

export function todayLine(d = new Date()) { return cap(`${WD[d.getDay()]}, ${d.getDate()} ${MONTHS_G[d.getMonth()]}`); }
export function dateLong(d) { return `${d.getDate()} ${MONTHS_G[d.getMonth()]}, ${WD[d.getDay()]}`; }
export function dateMid(d) { return `${d.getDate()} ${MONTHS_G[d.getMonth()]}`; }
export function hhmm(d) { return `${pad(d.getHours())}:${pad(d.getMinutes())}`; }

export function daysUntil(d) {
  return Math.round((startOfDay(d) - startOfDay(new Date())) / 86400000);
}
export function plural(n, one, few, many) {
  const a = Math.abs(n) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}
export function inDays(n) {
  if (n === 0) return 'сегодня';
  if (n === 1) return 'завтра';
  if (n < 0) return `просрочено на ${-n} ${plural(-n, 'день', 'дня', 'дней')}`;
  return `через ${n} ${plural(n, 'день', 'дня', 'дней')}`;
}

// ISO UTC -> «завтра в 09:00» в часовом поясе устройства
export function whenText(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const n = daysUntil(d);
  const day = n === 0 ? 'сегодня' : n === 1 ? 'завтра' : n === 2 ? 'послезавтра' : dateMid(d);
  return `${day} в ${hhmm(d)}`;
}

export const FREQ = { once: 'Один раз', daily: 'Каждый день', weekly: 'Каждую неделю', monthly: 'Каждый месяц', yearly: 'Каждый год' };
export const FREQ_SHORT = { once: 'разово', daily: 'ежедневно', weekly: 'еженедельно', monthly: 'ежемесячно', yearly: 'ежегодно' };

export function uuid() {
  if (crypto?.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0; return (c === 'x' ? r : (r & 3) | 8).toString(16);
  });
}

// Деньги: «1 234 567 ₽», настоящий минус, неразрывные пробелы.
const thousands = (s) => s.replace(/\B(?=(\d{3})+(?!\d))/g, '\u00a0');
export function rub(n, { sign = false } = {}) {
  if (n == null || !Number.isFinite(+n)) return '—';
  const v = Math.round(+n);
  return `${v < 0 ? '\u2212' : sign && v > 0 ? '+' : ''}${thousands(Math.abs(v).toString())}\u00a0₽`;
}
export function numSp(n) {
  if (n == null || !Number.isFinite(+n)) return '—';
  const [i, d] = String(+n).split('.');
  return thousands(i) + (d ? ',' + d : '');
}
