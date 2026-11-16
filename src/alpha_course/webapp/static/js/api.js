// Клиент API. Режимы: ?mock=1 (фейковые данные), ?dev=1 (X-Dev-User), иначе initData Telegram.
import { tg } from './tg.js';

const qs = new URLSearchParams(location.search);
export const MODE = qs.get('mock') ? 'mock' : qs.get('dev') ? 'dev' : 'prod';
const BASE = (qs.get('api') || '').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
  get friendly() {
    if (this.status === 0) return 'Нет связи с сервером. Проверь интернет и попробуй ещё раз.';
    if (this.status === 429) return this.message || 'Слишком много запросов. Подожди минуту и попробуй снова.';
    if (this.status === 503 && this.message) return this.message;
    if (this.status >= 500) return 'Сервер сейчас не отвечает. Это не ты — попробуй чуть позже.';
    if (this.status === 401) return 'Не удалось подтвердить, что это ты. Закрой Mini App и открой его снова из бота.';
    return this.message || 'Что-то пошло не так.';
  }
}

async function request(method, path, body) {
  if (MODE === 'mock') {
    try { return await (await import('./mock.js')).handle(method, path, body); }
    catch (e) { throw new ApiError(e.status ?? 500, e.error ?? 'error', e.message); }
  }
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (MODE === 'dev') headers['X-Dev-User'] = qs.get('user') || '4242';
  else headers['X-Telegram-Init-Data'] = tg.initData;
  let res;
  try {
    res = await fetch(BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    throw new ApiError(0, 'network', 'Нет связи с сервером.');
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* пустой ответ */ }
  if (!res.ok) throw new ApiError(res.status, data?.error || 'error', data?.message || '');
  return data;
}

const get = (p) => request('GET', p);
const post = (p, b = {}) => request('POST', p, b);
const put = (p, b) => request('PUT', p, b);
const patch = (p, b) => request('PATCH', p, b);
const del = (p) => request('DELETE', p);

// PDF: в Telegram файл уходит в чат с ботом (скачивание в вебвью ненадёжно), в браузере скачивается.
export async function getPdf(path, body, filename) {
  const viaChat = tg.inTelegram && MODE === 'prod';
  if (MODE === 'mock') { await new Promise((r) => setTimeout(r, 700)); return { mock: true, sent: tg.inTelegram }; }
  if (viaChat) return post(path, { ...body, send: true });
  const headers = { 'Content-Type': 'application/json' };
  if (MODE === 'dev') headers['X-Dev-User'] = qs.get('user') || '4242';
  let res;
  try { res = await fetch(BASE + path, { method: 'POST', headers, body: JSON.stringify(body) }); }
  catch (e) { throw new ApiError(0, 'network', 'Нет связи с сервером.'); }
  if (!res.ok) {
    let data = null; try { data = await res.json(); } catch (e) { /* пусто */ }
    throw new ApiError(res.status, data?.error || 'error', data?.message || '');
  }
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 4000);
  return { sent: false };
}

export const api = {
  bootstrap: () => get('/api/bootstrap'),
  ask: (question, history) => post('/api/ask', { question, history }),
  askFree: (question) => post('/api/ask/free', { question }),
  simplify: (question, answer) => post('/api/simplify', { question, answer }),
  feedback: (vote) => post('/api/feedback', { vote }).catch(() => {}),
  click: (material_id) => post('/api/click', { material_id }).catch(() => {}),
  profile: () => get('/api/profile'),
  saveProfile: (fields) => put('/api/profile', { fields }),
  describe: (text) => post('/api/profile/describe', { text }),
  clearProfile: () => del('/api/profile'),
  onboardingDone: () => post('/api/onboarding/done', {}),
  journeyPreview: (body) => post('/api/journey/preview', body),
  journeyStart: (body) => post('/api/journey', body),
  journey: () => get('/api/journey'),
  journeyDone: (day) => post('/api/journey/done', { day }),
  journeyStop: () => del('/api/journey'),
  taxes: () => get('/api/taxes'),
  taxesRemind: () => post('/api/taxes/remind', {}),
  reminders: () => get('/api/reminders'),
  parseReminder: (text, timezone) => post('/api/reminders/parse', { text, timezone }),
  resolveTz: (city) => post('/api/reminders/timezone', { city }),
  createReminder: (body) => post('/api/reminders', body),
  patchReminder: (id, body) => patch(`/api/reminders/${id}`, body),
  snooze: (id, version, kind) => post(`/api/reminders/${id}/snooze`, { version, kind }),
  deleteReminder: (id, version) => del(`/api/reminders/${id}?version=${version}`),
  settings: () => get('/api/settings'),
  saveSettings: (body) => put('/api/settings', body),
  games: () => get('/api/games'),
  negStart: (scenario_id) => post('/api/negotiation/start', { scenario_id }),
  negTurn: (scenario_id, history, message) => post('/api/negotiation/turn', { scenario_id, history, message }),
  negFinish: (scenario_id, history) => post('/api/negotiation/finish', { scenario_id, history }),
  ideaCheck: (body) => post('/api/idea/check', body),
  wordle: () => get('/api/wordle'),
  myths: () => get('/api/myths'),
  wordleGuess: (word, attempt) => post('/api/wordle/guess', { word, attempt }),
  tools: () => get('/api/tools'),
  finmodelMeta: () => get('/api/finmodel/meta'),
  finmodelCalc: (body) => post('/api/finmodel/calc', body),
  finmodelExtract: (text) => post('/api/finmodel/extract', { text }),
  search: (q) => get(`/api/materials/search?q=${encodeURIComponent(q)}`),
};
