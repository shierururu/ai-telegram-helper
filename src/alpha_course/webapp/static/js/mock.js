// Фейковый бэкенд для ?mock=1. Данные вымышленные; сроки налогов — примеры, не справка.
// Доп. параметры: &fresh=1 (первый запуск), &tz=0 (нет города), &journey=0 (без плана), &taxes=0 (профиль без налогов), &slow=1.
import { toolsHandle } from './mock-tools.js';
const qs = new URLSearchParams(location.search);
const SLOW = qs.get('slow') ? 3 : 1;
const wait = (ms) => new Promise((r) => setTimeout(r, ms * SLOW + Math.random() * 120));
const fail = (status, error, message) => { throw { status, error, message }; };
const clone = (x) => JSON.parse(JSON.stringify(x));

const mat = (id, title, kind, kind_label, icon, access, extra = {}) => ({
  id, title, url: `https://example.org/m/${id}`, kind, kind_label, icon, access, is_paid: access !== 'free',
  price: null, duration: null, lessons: null, purchase_url: null, ...extra,
});
const M = {
  tax: mat('m-tax', 'Как выбрать налоговый режим для малого бизнеса', 'article', 'статья', '📄', 'free', { duration: '12 мин' }),
  usn: mat('m-usn', 'УСН для ИП: что считать и когда платить', 'article', 'статья', '📄', 'free', { duration: '9 мин' }),
  calc: mat('m-calc', 'Калькулятор налога на УСН', 'calculator', 'калькулятор', '🧮', 'free'),
  pod: mat('m-pod', 'Первые клиенты без бюджета на рекламу', 'podcast', 'подкаст', '🎧', 'free', { duration: '24 мин' }),
  web: mat('m-web', 'Вебинар: как выстроить простую воронку продаж', 'webinar', 'вебинар', '🎥', 'free', { duration: '48 мин' }),
  gl: mat('m-gl', 'Словарь предпринимателя: эквайринг, ККТ, УСН', 'glossary', 'словарь', '📖', 'free'),
  doc: mat('m-doc', 'Чек-лист: документы для открытия ИП', 'document', 'документ', '📋', 'free'),
  course: mat('m-course', 'Налоги и учёт для малого бизнеса', 'course', 'курс', '🎓', 'paid', { price: '4 900 ₽', lessons: 14, purchase_url: 'https://example.org/buy/m-course' }),
  sales: mat('m-sales', 'Продажи без скидок: системный курс', 'course', 'курс', '🎓', 'paid', { price: '6 500 ₽', lessons: 18, purchase_url: 'https://example.org/buy/m-sales' }),
  sub: mat('m-sub', 'Библиотека практикумов по маркетингу', 'course', 'практикумы', '🗂️', 'subscription', { lessons: 40 }),
};

const S = {
  fresh: !!qs.get('fresh'),
  onboarded: !qs.get('fresh'),
  fields: qs.get('fresh') ? {} : { stage: 'запуск', industry: 'общепит', goal: 'найти клиентов', legal_form: qs.get('taxes') === '0' ? null : 'ИП', tax_regime: qs.get('taxes') === '0' ? null : 'УСН «Доходы»', employees: qs.get('taxes') === '0' ? null : false, level: 'novice' },
  settings: { timezone: qs.get('tz') === '0' ? null : 'Europe/Moscow', quiet_enabled: true, quiet_start: '22:00', quiet_end: '08:00', weekly_limit: 7, enabled: true, weekly_summary: false, weekly_summary_asked: false },
  journey: null, reminders: [], rid: 3,
};

const LABELS = { stage: 'Этап', industry: 'Сфера', goal: 'Цель', legal_form: 'Форма', tax_regime: 'Налоги', employees: 'Сотрудники', level: 'Опыт' };
function profile() {
  const f = {}; const labels = {};
  for (const k of Object.keys(LABELS)) {
    f[k] = S.fields[k] ?? null;
    if (f[k] !== null) labels[LABELS[k]] = k === 'employees' ? (f[k] ? 'есть' : 'нет') : k === 'level' ? (f[k] === 'novice' ? 'новичок' : 'есть опыт') : f[k];
  }
  return { fields: f, labels };
}

// ---------- время ----------
const pad = (n) => String(n).padStart(2, '0');
const atLocal = (d, hh, mm) => new Date(d.getFullYear(), d.getMonth(), d.getDate(), hh, mm, 0);
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const WD = ['воскресенье', 'понедельник', 'вторник', 'среду', 'четверг', 'пятницу', 'субботу'];
const MS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
const localIso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`;
function timing(freq, d) {
  const t = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (freq === 'daily') return `каждый день в ${t}`;
  if (freq === 'weekly') return `${['каждое воскресенье', 'каждый понедельник', 'каждый вторник', 'каждую среду', 'каждый четверг', 'каждую пятницу', 'каждую субботу'][d.getDay()]} в ${t}`;
  if (freq === 'monthly') return `каждое ${d.getDate()}-е число в ${t}`;
  if (freq === 'yearly') return `каждый год ${d.getDate()} ${MS[d.getMonth()]} в ${t}`;
  return `${d.getDate()} ${MS[d.getMonth()]} в ${t}`;
}
function mkReminder(event, freq, d, link = null) {
  return { id: 'r' + (S.rid++).toString(16).padStart(6, '0'), version: 1, event, frequency: freq, timing: timing(freq, d), next_due: d.toISOString(), timezone: S.settings.timezone || 'Europe/Moscow', link, _local: d.getTime() };
}
function pubR(r) { const { _local, ...x } = r; return clone(x); }

// ---------- старт ----------
{
  const now = new Date();
  S.reminders.push(mkReminder('Оплатить аренду точки', 'monthly', atLocal(addDays(now, 3), 10, 0)));
  S.reminders.push(mkReminder('Изучить: Как выбрать налоговый режим', 'weekly', atLocal(addDays(now, 1), 19, 30), { url: M.tax.url, title: M.tax.title }));
  if (S.fresh) S.reminders = [];
}

const TASKS = [
  ['Прослушай подкаст и выпиши 3 идеи, где найти первых клиентов', M.pod],
  ['Прочитай статью про удержание гостей и отметь 2 приёма для себя', M.usn],
  ['Выпиши три идеи, как привлечь первых гостей в кофейню', M.web],
  ['Выбери два способа приёма оплаты и запиши плюсы каждого', M.gl],
  ['Нарисуй простую воронку: откуда клиент узнаёт о тебе и покупает', M.web],
  ['Проверь цены по калькулятору и реши, что подправить', M.calc],
  ['Подведи итог недели: что сработало, а что убрать', M.tax],
];
function buildJourney(goal, time, startOffset, doneDays) {
  const [hh, mm] = time.split(':').map(Number);
  const start = addDays(new Date(), -startOffset);
  return { id: 'j-' + Date.now(), goal, timezone: S.settings.timezone || 'Europe/Moscow', time, status: 'active', _done: new Set(doneDays),
    _steps: TASKS.map(([task, m], i) => ({ day: i + 1, task, title: m.title, url: m.url, material_id: m.id, due: atLocal(addDays(start, i), hh, mm).toISOString() })) };
}
function pubJ(j) {
  if (!j) return null;
  const idx = j._steps.findIndex((s) => new Date(s.due) > new Date()); // первый будущий
  const todayDay = idx === -1 ? 7 : idx === 0 ? 1 : idx; // текущий день = последний наступивший
  const steps = j._steps.map((s) => {
    const done = j._done.has(s.day);
    const state = done ? 'done' : s.day === todayDay ? 'today' : s.day > todayDay ? 'locked' : 'missed';
    return { ...s, done, state };
  });
  if (steps.every((s) => s.done)) j.status = 'done';
  return { id: j.id, goal: j.goal, timezone: j.timezone, time: j.time, status: j.status, done_days: j._done.size, steps };
}
if (!S.fresh && qs.get('journey') !== '0') S.journey = buildJourney('Найти первых клиентов для кофейни', '09:00', 2, [1, 2]);

// ---------- консультация ----------
const rec = (m, why) => ({ material: m, why });
function reply(o) {
  return { kind: 'answer', status: 'answered', summary: '', steps: [], personal: '', sources: [], recommendations: [], followups: [], clarification: null, profile_updates: {}, excerpt: '', reminder_event: null, ...o };
}
function ask(q) {
  const t = q.toLowerCase();
  if (/^(привет|здравствуй|добрый|хай)/.test(t)) return reply({ kind: 'greeting', status: 'local', summary: 'Привет! Я помогаю разобраться с бизнесом по материалам из базы знаний: налоги, клиенты, запуск. О чём подумаем?', followups: ['Как выбрать налоговый режим?', 'Где взять первых клиентов?'] });
  if (/^(спасибо|благодарю)/.test(t)) return reply({ kind: 'thanks', status: 'local', summary: 'Пожалуйста! Если появятся вопросы — спрашивай.' });
  if (/напомни/.test(t)) return reply({ kind: 'reminder', status: 'local', summary: 'Могу поставить напоминание. Проверь текст и время — и подтверди.', reminder_event: q.replace(/^.*?напомни(ть)?\s*(мне)?\s*/i, '').trim() || 'Важное дело' });
  if (/налог.*(срок|когда|календар)|когда платить/.test(t)) return reply({ kind: 'tax', status: 'local', summary: 'Ближайшие сроки я собираю по твоему профилю: форме бизнеса, режиму и наличию сотрудников. Открой календарь — там всё по датам.', followups: ['Какой режим выбрать?'] });
  if (/погод|рецепт|футбол|гороскоп|анекдот/.test(t)) return reply({ kind: 'offtopic', status: 'local', summary: 'Это вне моей темы: я отвечаю про бизнес по материалам базы знаний. Могу помочь с налогами, клиентами или запуском.', followups: ['Как выбрать налоговый режим?', 'Где взять клиентов?', 'Что нужно для открытия ИП?'] });
  if (/блокчейн|крипт|nft|майнинг/.test(t)) return reply({ status: 'no_evidence', summary: 'В базе знаний нет материалов по этой теме, поэтому не буду выдумывать ответ. Ближайшее, что есть, — про общие основы цифровых платежей.', recommendations: [rec(M.gl, 'Объясняет базовые термины, на которые можно опереться'), rec(M.web, 'Про то, как строить продажи онлайн')], followups: ['Как принимать оплату от клиентов?', 'Что такое эквайринг?'] });
  if (/недоступ|сбой/.test(t)) return reply({ status: 'unavailable', summary: 'Сейчас не получается подготовить развёрнутый ответ. Вот материалы, которые точно по теме.', recommendations: [rec(M.tax, 'Основы выбора режима')] });
  const words = t.split(/\s+/).filter(Boolean);
  if (words.length <= 2 && !/налог|клиент|ип\b|усн/.test(t)) return reply({ kind: 'clarify', status: 'local', summary: '', clarification: { question: 'Уточни, пожалуйста: что именно тебя интересует?', options: ['Как начать бизнес', 'Как начать продавать', 'С чего начать учёбу'] } });
  if (/налог|усн|режим|ип\b/.test(t)) return reply({
    summary: 'Для небольшого бизнеса чаще всего выбирают между самозанятостью (НПД) и УСН. Выбор зависит от дохода, наличия сотрудников и вида деятельности.',
    steps: ['Прикинь годовой доход и есть ли у тебя сотрудники.', 'Сравни ставку: УСН «Доходы» — с выручки, «Доходы минус расходы» — с прибыли.', 'Посчитай налог в калькуляторе на своих цифрах.', 'Подай уведомление о переходе в установленный срок.'],
    personal: 'Ты на этапе запуска в общепите — обычно берут УСН «Доходы», если расходов немного. Если не уверен, посчитай оба варианта.',
    sources: [M.tax, M.usn], recommendations: [rec(M.calc, 'Посчитаешь налог на своих цифрах за пару минут'), rec(M.course, 'Системно разберёшься с налогами и учётом на практике'), rec(M.gl, 'Пояснит незнакомые термины простыми словами')],
    followups: ['Когда платить налог по УСН?', 'Объясни проще', 'Что если сотрудников нет?'], excerpt: 'Сравнение режимов по доходам и ставкам…',
    // A/B-демо: ?ab=B показывает вариант B (персональный блок про платный курс).
    offer: { variant: qs.get('ab') === 'B' ? 'B' : 'A', material: M.course, pitch: qs.get('ab') === 'B' ? { title: M.course.title, price: M.course.price || 'платный курс', benefit: 'Курс о налогах и учёте для малого бизнеса: как выбрать режим и не ошибаться с отчётностью.', fit: 'Подобран под твой профиль: запускаю, общепит, ИП, УСН.', note: 'Программа и условия — на странице курса.' } : null },
  });
  if (/клиент|продаж|реклам|кофейн|кафе/.test(t)) return reply({
    summary: 'Первых клиентов проще всего искать там, где они уже есть рядом: соседи, местные чаты, партнёры и повторные визиты.',
    steps: ['Опиши, кто твой идеальный гость и чем ты отличаешься.', 'Выбери 2 канала без бюджета: локальные сообщества и партнёрства.', 'Предложи простой повод вернуться: бонус за второй визит.', 'Через неделю посмотри, что дало отклики, и усиль это.'],
    personal: 'Для кофейни на старте хорошо работают соседские чаты и карты — они не требуют бюджета.',
    sources: [M.pod], recommendations: [rec(M.pod, 'Конкретные приёмы поиска первых клиентов без рекламы'), rec(M.sales, 'Подойдёт, когда захочешь выстроить продажи системно'), rec(M.sub, 'Много коротких практикумов для регулярных шагов')],
    followups: ['Как удержать клиентов?', 'Сколько тратить на рекламу?'], profile_updates: /кофейн|кафе/.test(t) ? { industry: 'общепит' } : {},
  });
  return reply({ status: 'partial', summary: 'Точного ответа на этот вопрос в базе нет, но есть близкие материалы. Уточни, что именно нужно: налоги, клиенты или запуск?',
    recommendations: [rec(M.doc, 'Базовый чек-лист по запуску'), rec(M.tax, 'Общая картина по налогам')], followups: ['Как выбрать налоговый режим?', 'Где взять клиентов?'],
    clarification: null });
}

// ---------- налоги ----------
function taxes() {
  const f = S.fields;
  if (!f.legal_form || !f.tax_regime || f.employees === null || f.employees === undefined) {
    return { ready: false, missing: ['legal_form', 'tax_regime', 'employees'].filter((k) => f[k] === null || f[k] === undefined) };
  }
  const now = new Date();
  const mk = (n, title, kind, note, shift = false) => { let d = addDays(now, n); const wd = d.getDay(); if (shift && (wd !== 6 && wd !== 0)) { d = addDays(d, wd === 5 ? 1 : 6 - wd); } return { date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, title, kind, note, shifted: shift }; };
  const items = [
    mk(4, 'Уплата страховых взносов ИП за себя (пример)', 'payment', 'Фиксированная часть взносов.'),
    mk(9, 'Авансовый платёж по УСН (пример)', 'payment', 'За прошедший квартал.', true),
    mk(16, 'Уведомление об исчисленных налогах (пример)', 'report', 'Подаётся до 25-го числа.'),
    mk(31, 'Налоговая декларация (пример)', 'report', 'Раз в год по итогам периода.'),
    mk(47, 'Уплата взносов 1% с дохода свыше лимита (пример)', 'payment', ''),
  ];
  return { ready: true, profile_labels: profile().labels, items, next: items[0] };
}

// ---------- reminders parse ----------
function parseReminder(text, tzIn) {
  const t = text.toLowerCase();
  const needs = [];
  if (!S.settings.timezone && !tzIn) needs.push('timezone');
  let freq = 'once';
  if (/каждый день|ежедневно/.test(t)) freq = 'daily';
  else if (/кажд\S*\s+(понедельник|вторник|сред\S*|четверг|пятниц\S*|суббот\S*|воскресенье)|еженедельно|раз в неделю/.test(t)) freq = 'weekly';
  else if (/ежемесячно|каждый месяц|раз в месяц/.test(t)) freq = 'monthly';
  else if (/ежегодно|каждый год/.test(t)) freq = 'yearly';
  const tm = t.match(/(\d{1,2})[:.](\d{2})/) || t.match(/\bв\s+(\d{1,2})\b(?!\s*(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря))/);
  let local_at = null;
  if (tm) {
    const hh = +tm[1], mm = tm[2] ? +tm[2] : 0;
    let d = new Date();
    if (/послезавтра/.test(t)) d = addDays(d, 2); else if (/завтра/.test(t)) d = addDays(d, 1);
    let at = atLocal(d, hh, mm);
    if (at < new Date() && !/сегодня|завтра/.test(t)) at = addDays(at, 1);
    local_at = localIso(at);
  } else needs.push('time');
  let event = text.replace(/(послезавтра|завтра|сегодня|каждый день|ежедневно|ежемесячно|еженедельно|ежегодно|кажд\S*\s+\S+|в\s+\d{1,2}([:.]\d{2})?)/gi, ' ').replace(/^(напомни(ть)?( мне)?)\s*/i, '').replace(/\s+/g, ' ').trim();
  if (!event) { needs.push('event'); event = ''; } else event = event[0].toUpperCase() + event.slice(1);
  return { event, local_at, timezone: S.settings.timezone || tzIn || null, frequency: freq, link: null, needs };
}

const CITY = { москва: 'Europe/Moscow', омск: 'Asia/Omsk', казань: 'Europe/Moscow', новосибирск: 'Asia/Novosibirsk', екатеринбург: 'Asia/Yekaterinburg', владивосток: 'Asia/Vladivostok', 'санкт-петербург': 'Europe/Moscow' };
function resolveCity(c) { const z = CITY[(c || '').trim().toLowerCase()]; if (!z) fail(400, 'unknown_city', 'Не нашёл такой город. Попробуй крупный город рядом или напиши в формате «Омск».'); return z; }

// ---------- слово дня ----------
const WORD = 'КАПИТАЛ';
function wordleDay() { return new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Moscow' }).format(new Date()); }
function wordleEval(guess) {
  const res = Array(WORD.length).fill('absent'); const left = {};
  for (let i = 0; i < WORD.length; i++) { if (guess[i] === WORD[i]) res[i] = 'correct'; else left[WORD[i]] = (left[WORD[i]] || 0) + 1; }
  for (let i = 0; i < WORD.length; i++) { if (res[i] !== 'correct' && left[guess[i]] > 0) { res[i] = 'present'; left[guess[i]]--; } }
  return res;
}

// ---------- игры ----------
const SCEN = [
  { id: 'unpaid', title: 'Клиент не платит по счёту', brief: 'Крупный клиент задерживает оплату уже три недели.', role: 'Владелец небольшой студии', goal: 'Получить оплату в ближайшие 5 дней без потери клиента', difficulty: 'легко', topic: 'дебиторская задолженность' },
  { id: 'discount', title: 'Клиент просит скидку 30%', brief: 'Постоянный заказчик требует снизить цену на треть.', role: 'Исполнитель услуги', goal: 'Сохранить маржу и договориться о разумной уступке', difficulty: 'средне', topic: 'ценообразование скидки' },
  { id: 'supplier', title: 'Поставщик повышает цену', brief: 'Поставщик объявил рост цен на 15% со следующего месяца.', role: 'Владелец кафе', goal: 'Удержать цену или получить компенсацию условиями', difficulty: 'средне', topic: 'договор с поставщиком' },
  { id: 'rent', title: 'Арендодатель поднимает аренду', brief: 'Владелец помещения хочет поднять ставку на 25%.', role: 'Арендатор точки', goal: 'Договориться об индексации и длинном сроке', difficulty: 'сложно', topic: 'аренда помещения' },
  { id: 'hire', title: 'Первый найм: зарплатные ожидания', brief: 'Сильный кандидат называет сумму выше вашего бюджета.', role: 'Основатель', goal: 'Нанять человека в рамках бюджета и сохранить мотивацию', difficulty: 'средне', topic: 'найм сотрудников' },
  { id: 'tax', title: 'Запрос пояснений от налоговой', brief: 'Инспектор просит пояснить расхождения в отчётности.', role: 'ИП на УСН', goal: 'Спокойно и по делу снять вопросы', difficulty: 'сложно', topic: 'налоговая проверка пояснения' },
];
const OPEN = { unpaid: 'Добрый день. Да, помню про счёт, но у нас сейчас кассовый разрыв. Давайте решим это как-нибудь потом?', discount: 'Слушайте, цены у вас кусаются. Либо минус 30%, либо я пойду к другим.', supplier: 'С первого числа цены растут на 15%. Это решение собственника, у меня нет полномочий его менять.', rent: 'Рынок вырос, так что с января ставка плюс 25%. Или освобождайте помещение.', hire: 'Мне нужно 200 тысяч, иначе мне нет смысла менять работу. Что можете предложить?', tax: 'В декларации расходятся показатели. Прошу в течение 5 дней дать письменные пояснения.' };
const REPL = ['Хм, допустим. А какие у вас конкретные условия?', 'Это звучит разумнее. Но мне нужны гарантии.', 'Понимаю вашу позицию. Предложите срок и сумму.', 'Ладно, давайте обсудим детали подробнее.', 'Вы меня почти убедили. Что ещё готовы предложить?', 'Хорошо, давайте зафиксируем договорённости.'];
let negMood = 40;

function gamesList() { return { games: [{ id: 'wordle', title: 'Слово дня', tagline: 'Угадай бизнес-термин за 6 попыток', kind: 'game' }, { id: 'myths', title: 'Миф или правда', tagline: 'Пять утверждений о бизнесе в день', kind: 'game' }, { id: 'breakeven', title: 'Точка безубыточности', tagline: 'Угадай, сколько продаж нужно, чтобы выйти в ноль', kind: 'game' }, { id: 'negotiation', title: 'Переговоры', tagline: 'Потренируйся договариваться с ИИ', kind: 'game' }, { id: 'idea', title: 'Проверка идеи', tagline: 'Оцени бизнес-идею по базе знаний', kind: 'tool' }], scenarios: SCEN }; }

function negTurn(b) {
  const t = (b.message || '').toLowerCase(); const mine = b.history.filter((x) => x.role === 'me').length + 1;
  let d = 6;
  if (/понима|предлаг|вариант|давайте|компромисс|выгод|график|част/.test(t)) d += 10;
  if (/должны|обязаны|требую|немедленно|срочно|суд/.test(t)) d -= 22;
  if (t.length < 12) d -= 6;
  negMood = Math.max(0, Math.min(100, (mine === 1 ? 40 : negMood) + d));
  const finished = negMood >= 85 || negMood <= 8 || mine >= 6;
  const outcome = !finished ? undefined : negMood >= 70 ? 'deal' : negMood <= 12 ? 'walkout' : 'no_deal';
  const tips = ['Сначала выясни интересы собеседника, а потом предлагай варианты.', 'Назови конкретный срок или сумму — расплывчатость ослабляет позицию.', 'Предложи обмен: уступка за уступку.', null, 'Зафиксируй договорённость письменно.', null];
  return { reply: outcome === 'walkout' ? 'Тогда нам не о чем говорить. Всего доброго.' : outcome === 'deal' ? 'Хорошо, по этим условиям договорились.' : REPL[mine - 1] || REPL[5], mood: negMood, tip: tips[mine - 1], turn: mine, finished, ...(outcome ? { outcome } : {}) };
}
function negFinish(b) {
  const sc = Math.round(Math.max(8, Math.min(96, negMood * 0.9 + b.history.filter((x) => x.role === 'me').length * 2)));
  return { score: sc, verdict: sc >= 70 ? 'Уверенные переговоры: вы держали позицию и предлагали варианты' : sc >= 45 ? 'Неплохо, но не хватило конкретики и встречных условий' : 'Нужна практика: давление вместо интересов не сработало',
    strengths: ['Вы сохраняли спокойный тон', 'Предложили хотя бы один вариант решения'], improvements: ['Называйте точные сроки и суммы', 'Сначала выясняйте интересы второй стороны'],
    better_phrases: [{ instead: 'Вы обязаны заплатить сегодня.', say: 'Давайте зафиксируем график: половина в пятницу, остаток до конца месяца.' }, { instead: 'Это слишком дорого.', say: 'Какой объём работ вы готовы сократить, чтобы уложиться в бюджет?' }],
    materials: [M.pod, M.web, M.doc], outcome: sc >= 70 ? 'deal' : sc >= 30 ? 'no_deal' : 'walkout' };
}
function ideaCheck(b) {
  const idea = (b.idea || '').trim();
  if (idea.length < 12 || /погод|кино|привет|анекдот/i.test(idea)) fail(400, 'not_business', 'Это не похоже на бизнес-идею. Опиши, что ты хочешь продавать и кому.');
  const bonus = (b.answers || []).filter((a) => a.answer && a.answer.trim()).length * 9;
  const score = Math.min(92, 46 + Math.min(idea.length, 120) / 6 + bonus + (b.city ? 4 : 0));
  const verdict = score >= 70 ? 'go' : score >= 50 ? 'test' : 'rethink';
  return { verdict, score: Math.round(score), summary: 'Идею можно проверять небольшим пилотом: по базе знаний виден понятный путь запуска, но есть вопросы по спросу и юридическому оформлению.',
    checklist: [{ title: 'Форма регистрации', status: 'ok', why: 'Для старта подойдёт самозанятость или ИП — это описано в материалах базы.', material: M.doc }, { title: 'Налоговый режим', status: 'unknown', why: 'Режим зависит от дохода и наличия сотрудников — этого в описании нет.', material: M.tax }, { title: 'Разрешения и санитарные требования', status: 'risk', why: 'Для работы с продуктами питания обычно нужны отдельные согласования.', material: null }, { title: 'Первые клиенты', status: 'ok', why: 'Есть бюджетные способы привлечения.', material: M.pod }],
    risks: ['Спрос не проверен на реальных покупателях', 'Возможны дополнительные разрешения'], questions: ['Сколько клиентов в месяц нужно для безубыточности?', 'Есть ли у вас помещение или формат без него?'],
    needs: { documents: ['Регистрация ИП или самозанятость', 'Договор аренды'], taxes: ['УСН или НПД'], permits: ['Санитарные требования'] },
    next_steps: ['Опиши целевого клиента одним абзацем', 'Посчитай точку безубыточности в калькуляторе', 'Проверь спрос на 5–10 реальных людях'], materials: [M.calc, M.tax, M.pod], grounded: !/нишев|космос/.test(idea) };
}

// ---------- роутинг ----------
export async function handle(method, fullPath, body) {
  await wait(method === 'GET' ? 220 : 320);
  const [path, query] = fullPath.split('?');
  const m = (re) => path.match(re);
  const key = `${method} ${path}`;

  if (key === 'GET /api/bootstrap') {
    return { profile: profile(), onboarded: S.onboarded, settings: clone(S.settings), journey: pubJ(S.journey), reminders: S.reminders.map(pubR),
      options: { stages: ['идея', 'запуск', 'работает до года', 'развитие'], industries: ['розница', 'общепит', 'услуги', 'онлайн-торговля и маркетплейсы', 'производство', 'строительство', 'логистика', 'IT и digital', 'образование', 'красота и здоровье', 'оптовая торговля', 'другое'],
        legal_forms: ['самозанятый', 'ИП', 'ООО', 'пока не зарегистрирован'], tax_regimes: ['НПД', 'УСН «Доходы»', 'УСН «Доходы минус расходы»', 'ПСН', 'АУСН', 'ОСНО', 'не знаю'], goals: ['найти клиентов', 'разобраться с налогами', 'запустить дело', 'снизить расходы', 'увеличить продажи', 'собрать команду'] },
      taxes_ready: taxes().ready, brand: 'Бизнес-навигатор' };
  }
  if (key === 'POST /api/ask') {
    if (!body.question || !body.question.trim()) fail(400, 'empty', 'Напиши вопрос.');
    const r = ask(body.question);
    Object.assign(S.fields, r.profile_updates);
    return r;
  }
  if (key === 'POST /api/ask/free') {
    return { kind: 'free', available: true, message: '', title: 'Первые шаги проверки спроса', links: [{ title: 'Пример источника', url: 'https://example.org' }], note: '🎯 Учтено: ИП, УСН, услуги', text: 'Общий совет: начни с проверки спроса — поговори с 5–10 потенциальными клиентами, прежде чем вкладываться. Оцени, сколько стоит привлечь одного клиента и сколько он приносит. Важные юридические и налоговые детали уточни у специалиста.' };
  }
  if (key === 'POST /api/simplify') {
    if (/сбой/.test(body.question || '')) return { text: '', terms: [] };
    return { text: 'Если совсем просто: налоговый режим — это «правило», по которому ты платишь налог. Самозанятым проще всего, ИП на УСН платят чуть сложнее, зато можно нанимать людей и расти.', terms: [{ term: 'УСН', meaning: 'упрощённая система налогообложения: платишь процент с дохода или с прибыли' }, { term: 'НПД', meaning: 'налог на профессиональный доход для самозанятых' }] };
  }
  if (key === 'POST /api/feedback' || key === 'POST /api/click') return { ok: true };
  if (key === 'GET /api/profile') return profile();
  if (key === 'PUT /api/profile') { Object.assign(S.fields, body.fields || {}); return profile(); }
  if (key === 'DELETE /api/profile') { S.fields = {}; return profile(); }
  if (key === 'POST /api/profile/describe') {
    const t = (body.text || '').toLowerCase(); const u = {};
    if (/кофейн|кафе|ресторан/.test(t)) u.industry = 'общепит';
    if (/магазин|розниц/.test(t)) u.industry = 'розница';
    if (/\bип\b/.test(t)) u.legal_form = 'ИП';
    if (/самозанят/.test(t)) u.legal_form = 'самозанятый';
    if (/только открыл|запуска|запускаю/.test(t)) u.stage = 'запуск';
    if (/нет сотрудник|один работаю/.test(t)) u.employees = false; else if (/сотрудник|нанял/.test(t)) u.employees = true;
    if (/клиент/.test(t)) u.goal = 'найти клиентов';
    Object.assign(S.fields, u);
    return { updates: u, profile: profile() };
  }
  if (key === 'POST /api/onboarding/done') { S.onboarded = true; return { ok: true }; }
  if (key === 'POST /api/route' || key === 'POST /api/route/done') fail(410, 'gone', 'Используй план на 7 дней.');
  if (key === 'POST /api/journey/preview') {
    const goal = body.goal || S.fields.goal; if (!goal) fail(409, 'need_goal', 'Сначала выбери цель.');
    if (!S.settings.timezone) fail(409, 'need_timezone', 'Нужен город, чтобы присылать шаги вовремя.');
    return { goal, time: body.time, timezone: S.settings.timezone, steps: TASKS.map(([task, mm], i) => ({ day: i + 1, task, title: mm.title, url: mm.url, material_id: mm.id })) };
  }
  if (key === 'POST /api/journey') {
    S.journey = buildJourney(body.goal, body.time, 0, []);
    return pubJ(S.journey);
  }
  if (key === 'GET /api/journey') return pubJ(S.journey);
  if (key === 'POST /api/journey/done') {
    const j = pubJ(S.journey); const s = j?.steps.find((x) => x.day === body.day);
    if (!s) fail(404, 'not_found', 'Такого дня нет.');
    if (s.state === 'locked') fail(409, 'day_not_reached', 'Этот день ещё не наступил.');
    S.journey._done.add(body.day);
    return pubJ(S.journey);
  }
  if (key === 'DELETE /api/journey') { S.journey = null; return { ok: true }; }
  if (key === 'GET /api/taxes') return taxes();
  if (key === 'POST /api/taxes/remind') { const t = taxes(); if (!t.ready) fail(409, 'not_ready', 'Сначала заполни профиль.'); const created = S.__taxReminded ? 0 : t.items.length - 1; S.__taxReminded = true; return { created }; }
  if (key === 'GET /api/reminders') return { reminders: S.reminders.map(pubR) };
  if (key === 'POST /api/reminders/parse') return parseReminder(body.text || '', body.timezone);
  if (key === 'POST /api/reminders/timezone') { const tz = resolveCity(body.city); S.settings.timezone = tz; return { timezone: tz }; }
  if (key === 'POST /api/reminders') {
    const dup = S.reminders.find((r) => r._op === body.operation_id); if (dup) return pubR(dup);
    if (S.reminders.length >= 5) fail(409, 'limit', 'Уже 5 напоминаний — это максимум. Отключи ненужное, чтобы добавить новое.');
    if (!body.event || !body.local_at) fail(400, 'invalid', 'Нужны название и время.');
    const r = mkReminder(body.event, body.frequency, new Date(body.local_at), body.link || null); r._op = body.operation_id; S.reminders.push(r); return pubR(r);
  }
  const rm = m(/^\/api\/reminders\/(\w+)(\/snooze)?$/);
  if (rm) {
    const r = S.reminders.find((x) => x.id === rm[1]); if (!r) fail(404, 'not_found', 'Напоминание уже не существует.');
    if (method === 'DELETE') { const v = +new URLSearchParams(query).get('version'); if (v !== r.version) fail(409, 'conflict', 'Напоминание изменилось. Обновил список.'); S.reminders = S.reminders.filter((x) => x !== r); return { ok: true }; }
    if (body.version !== r.version) fail(409, 'conflict', 'Напоминание изменилось. Обновил список.');
    if (rm[2]) { const d = new Date(r.next_due); d.setTime(d.getTime() + (body.kind === 'h1' ? 3600e3 : 86400e3)); r.next_due = d.toISOString(); r.timing = timing(r.frequency, d); r.version++; return pubR(r); }
    if (method === 'PATCH') { if (body.frequency) r.frequency = body.frequency; const d = new Date(r.next_due); r.timing = timing(r.frequency, d); r.version++; return pubR(r); }
  }
  if (key === 'GET /api/settings') return clone(S.settings);
  if (key === 'PUT /api/settings') {
    const { city, ...rest } = body; if (city) S.settings.timezone = resolveCity(city);
    Object.assign(S.settings, rest); return clone(S.settings);
  }
  if (key === 'GET /api/games') return gamesList();
  if (key === 'POST /api/negotiation/start') { const sc = SCEN.find((x) => x.id === body.scenario_id); if (!sc) fail(404, 'not_found', 'Сценарий не найден.'); negMood = 40; return { scenario: sc, opening: OPEN[sc.id], max_turns: 6, mood: 40 }; }
  if (key === 'POST /api/negotiation/turn') { if (!(body.message || '').trim()) fail(400, 'empty', 'Напиши реплику.'); if (/сбой/.test(body.message)) fail(500, 'error', 'Сбой'); return negTurn(body); }
  if (key === 'POST /api/negotiation/finish') return negFinish(body);
  if (key === 'POST /api/idea/check') return ideaCheck(body);
  if (key === 'GET /api/myths') return { date: wordleDay(), cards: [
    { id: 'npd-staff', statement: 'Самозанятый на НПД может нанимать сотрудников по трудовому договору.', truth: false, why: 'На НПД нельзя иметь работников по трудовым договорам. Нужны сотрудники — выбирай ИП или ООО.', material: M.tax },
    { id: 'break-even', statement: 'Точка безубыточности — это объём продаж, при котором бизнес выходит в ноль, а не в прибыль.', truth: true, why: 'В этой точке выручка покрывает все расходы. Прибыль начинается только после неё.', material: M.calc },
    { id: 'revenue-profit', statement: 'Выручка — это то же самое, что прибыль.', truth: false, why: 'Выручка — все деньги от продаж. Прибыль остаётся после вычета расходов и налогов.', material: null },
    { id: 'ip-contributions', statement: 'Если у ИП нет дохода, страховые взносы за себя платить не нужно.', truth: false, why: 'Фиксированные страховые взносы ИП платит независимо от выручки, пока предприниматель не снят с учёта.', material: M.tax },
    { id: 'usn-income', statement: 'На УСН «Доходы» налог считается с выручки, а не с прибыли.', truth: true, why: 'Расходы на размер налога не влияют. Поэтому «Доходы» удобны при низких расходах.', material: M.pod }] };
  if (key === 'GET /api/wordle') return { date: wordleDay(), number: 1, length: WORD.length, max_tries: 6, hint: '_______ — это совокупность ресурсов компании, которые она использует для получения прибыли.' };
  if (key === 'POST /api/wordle/guess') {
    const w = (body.word || '').toUpperCase();
    if (w.length !== WORD.length || !/^[А-ЯЁ]+$/.test(w) || w === 'ААААААА') fail(400, 'invalid_word', `Нужно слово из ${WORD.length} русских букв.`);
    const result = wordleEval(w); const solved = w === WORD; const finished = solved || body.attempt >= 6;
    return finished ? { result, solved, finished, answer: WORD, definition: 'Капитал — совокупность ресурсов компании, которые она использует для получения прибыли.', material: { id: 'm-gl', title: M.gl.title, url: M.gl.url } } : { result, solved, finished };
  }
  if (key === 'GET /api/materials/search') { const q = (new URLSearchParams(query).get('q') || '').toLowerCase(); return { items: Object.values(M).filter((x) => x.title.toLowerCase().includes(q)).slice(0, 8) }; }
  const tool = await toolsHandle(key, body);
  if (tool !== undefined) return tool;
  fail(404, 'not_found', 'Не нашёл такой запрос.');
}
