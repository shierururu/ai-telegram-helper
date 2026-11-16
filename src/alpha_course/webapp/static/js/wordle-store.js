// «Слово дня»: локальное хранилище (только в браузере): партии по датам и статистика.
const KEY = 'wordle:';
const read = (k) => { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } };
const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* без хранилища играем в рамках сессии */ } };

export const moscowDate = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow' }).format(new Date());
const shiftDay = (ymd, n) => { const d = new Date(ymd + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

export const loadDay = (date) => read(KEY + 'day:' + date);
export const saveDay = (day) => write(KEY + 'day:' + day.date, day);

const EMPTY = { games: 0, wins: 0, cur: 0, best: 0, dist: [0, 0, 0, 0, 0, 0], lastWin: null };
export const loadStats = () => { const s = read(KEY + 'stats') || {}; return { ...EMPTY, ...s, dist: Array.isArray(s.dist) && s.dist.length === 6 ? s.dist.slice() : [0, 0, 0, 0, 0, 0] }; };

// Фиксируем завершённую партию один раз.
export function recordFinish(day) {
  if (day.counted) return loadStats();
  const s = loadStats();
  s.games += 1;
  if (day.solved) {
    s.wins += 1;
    s.cur = s.lastWin === shiftDay(day.date, -1) ? s.cur + 1 : 1;
    s.best = Math.max(s.best, s.cur);
    s.lastWin = day.date;
    const i = Math.min(day.guesses.length, 6) - 1; s.dist[i] = (s.dist[i] || 0) + 1;
  } else s.cur = 0;
  write(KEY + 'stats', s);
  day.counted = true; saveDay(day);
  return s;
}

// Серия, которую честно показывать сегодня: если вчера/сегодня не выиграно — 0.
export function streakNow() {
  const s = loadStats(); const t = moscowDate();
  return s.lastWin === t || s.lastWin === shiftDay(t, -1) ? s.cur : 0;
}
export const playedToday = () => !!loadDay(moscowDate())?.finished;

// секунд до ближайшей полуночи по Москве
export function secondsToMidnight() {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Moscow', hourCycle: 'h23', hour: '2-digit', minute: '2-digit', second: '2-digit' }).formatToParts(new Date()).map((x) => [x.type, +x.value]));
  return 86400 - (p.hour * 3600 + p.minute * 60 + p.second);
}
export function fmtLeft(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return [h, m, s].map((x) => String(x).padStart(2, '0')).join(':');
}
