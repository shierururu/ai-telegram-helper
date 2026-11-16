// Фейковые «Инструменты» для ?mock=1: финмодель. Данные вымышленные.
// Математика повторяет application/finmodel.py (упрощённо), чтобы цифры в моке были правдоподобными.
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fail = (status, error, message) => { throw { status, error, message }; };

const mat = (id, title, kind, kind_label, icon, extra = {}) => ({
  id, title, url: `https://example.org/m/${id}`, kind, kind_label, icon, access: 'free', is_paid: false,
  price: null, duration: null, lessons: null, purchase_url: null, ...extra,
});
const CALCS = [
  mat('m-calc', 'Калькулятор налога на УСН', 'calculator', 'калькулятор', '🧮'),
  mat('m-calc-pay', 'Калькулятор зарплаты и взносов', 'calculator', 'калькулятор', '🧮'),
  mat('m-calc-npd', 'Калькулятор налога для самозанятых', 'calculator', 'калькулятор', '🧮'),
];
// ---------- финмодель ----------
const REGIMES = { npd: 'НПД (самозанятый)', usn_income: 'УСН «Доходы»', usn_income_expense: 'УСН «Доходы минус расходы»' };
const RATES = { npd_person: 0.04, npd_company: 0.06, usn_income: 0.06, usn_ie: 0.15 };
const CONTRIB = 0.3;

function num(raw, name, { low = 0, required = true } = {}) {
  if (raw == null || raw === '') { if (required) fail(400, 'invalid_input', `Заполни поле «${name}».`); return 0; }
  const v = typeof raw === 'string' ? parseFloat(raw.replace(/[\s ]/g, '').replace(',', '.')) : Number(raw);
  if (!Number.isFinite(v)) fail(400, 'invalid_input', `Поле «${name}» должно быть числом.`);
  if (v < low || v > 1e10) fail(400, 'invalid_input', `Поле «${name}»: допустимы значения от ${low} до 10 000 000 000.`);
  return v;
}
function parse(b) {
  const regime = b.regime || 'usn_income';
  if (!REGIMES[regime]) fail(400, 'invalid_input', 'Неизвестный налоговый режим.');
  return {
    price: num(b.price, 'цена за единицу', { low: 0.01 }), unit_cost: num(b.unit_cost, 'переменные затраты на единицу', { required: false }),
    units: num(b.units, 'продаж в месяц'), fixed: num(b.fixed, 'постоянные расходы', { required: false }),
    payroll: num(b.payroll, 'зарплаты', { required: false }), investment: num(b.investment, 'стартовые вложения', { required: false }),
    regime, client: b.client === 'company' ? 'company' : 'person', rate: null,
  };
}
const rateOf = (i) => i.rate ?? (i.regime === 'npd' ? (i.client === 'company' ? RATES.npd_company : RATES.npd_person) : i.regime === 'usn_income' ? RATES.usn_income : RATES.usn_ie);
function profitAt(i, units) {
  const revenue = i.price * units, variable = i.unit_cost * units, contributions = i.payroll * CONTRIB;
  const fixedTotal = i.fixed + i.payroll + contributions, rate = rateOf(i);
  let tax;
  if (i.regime === 'usn_income_expense') tax = revenue > 0 ? Math.max(Math.max(0, revenue - variable - fixedTotal) * rate, revenue * 0.01) : 0;
  else if (i.regime === 'usn_income') { const g = revenue * rate; tax = Math.max(0, g - Math.min(contributions, g * 0.5)); }
  else tax = revenue * rate;
  return { profit: revenue - variable - fixedTotal - tax, revenue, tax, contributions, fixedTotal, variable };
}
function breakEven(i) {
  if (i.price <= i.unit_cost) return null;
  if (profitAt(i, 0).profit >= 0) return 0;
  let hi = 1;
  while (profitAt(i, hi).profit < 0) { hi *= 2; if (hi > 1e9) return null; }
  let lo = hi > 1 ? hi / 2 : 0;
  for (let k = 0; k < 60; k++) { const mid = (lo + hi) / 2; if (profitAt(i, mid).profit < 0) lo = mid; else hi = mid; }
  return Math.ceil(hi - 1e-9);
}
const r0 = (x) => Math.round(x);
const r1 = (x) => Math.round(x * 10) / 10;

function calc(body) {
  const i = parse(body);
  const p = profitAt(i, i.units);
  const margin = i.price - i.unit_cost, even = breakEven(i);
  const safety = even != null && even > 0 && i.units > 0 ? (i.units - even) / i.units * 100 : null;
  const payback = p.profit > 0 && i.investment > 0 ? i.investment / p.profit : null;
  const warnings = [];
  if (margin <= 0) warnings.push('Цена не выше переменных затрат: каждая продажа приносит убыток.');
  if (even != null && even > i.units && i.units > 0) warnings.push(`Плановых продаж (${i.units}) не хватает до точки безубыточности (${even}).`);
  if (i.regime === 'npd') {
    if (i.payroll > 0) warnings.push('На НПД нельзя нанимать сотрудников по трудовым договорам.');
    if (p.revenue * 12 > 2400000) warnings.push('Доход за год выше 2,4 млн ₽: лимит НПД превышен.');
  }
  if (i.regime === 'usn_income_expense' && p.revenue > 0 && Math.abs(p.tax - p.revenue * 0.01) < 1e-6) warnings.push('Действует минимальный налог 1% от выручки: расходы почти съедают базу.');

  const regimes = Object.entries(REGIMES).map(([key, label]) => {
    const v = { ...i, regime: key, rate: key === i.regime ? i.rate : null };
    const r = profitAt(v, v.units);
    return { regime: key, label, tax: r0(r.tax), profit: r0(r.profit), available: !(key === 'npd' && (i.payroll > 0 || p.revenue * 12 > 2400000)), current: key === i.regime };
  });
  const avail = regimes.filter((x) => x.available);
  const best = avail.length ? avail.reduce((a, b) => (b.profit > a.profit ? b : a)) : null;
  regimes.forEach((x) => { x.best = x === best; });

  const scen = (label, uk = 1, pk = 1) => { const v = { ...i, price: i.price * pk, units: i.units * uk }; const r = profitAt(v, v.units); return { label, units: r1(v.units), revenue: r0(r.revenue), profit: r0(r.profit) }; };
  const verdict = p.profit < 0 ? 'loss' : even != null && safety != null && safety < 20 ? 'tight' : 'ok';
  return {
    regime: i.regime, regime_label: REGIMES[i.regime], revenue: r0(p.revenue), variable: r0(p.variable), fixed_total: r0(p.fixedTotal),
    contributions: r0(p.contributions), tax: r0(p.tax), tax_rate: Math.round(rateOf(i) * 10000) / 100, profit: r0(p.profit),
    unit_margin: Math.round(margin * 100) / 100, margin_pct: r1(margin / i.price * 100), break_even_units: even,
    break_even_revenue: even == null ? null : r0(even * i.price), safety_pct: safety == null ? null : r1(safety),
    payback_months: payback == null ? null : r1(payback),
    scenarios: [scen('Пессимистично: −30% продаж', 0.7), scen('По плану'), scen('Оптимистично: +30% продаж', 1.3), scen('Цена −10%', 1, 0.9), scen('Цена +10%', 1, 1.1)],
    regimes, warnings, verdict,
  };
}

// «ИИ» достаёт числа из текста: грубые регулярки вместо модели.
function extract(text) {
  const t = String(text || '').toLowerCase();
  const find = (re) => {
    const m = t.match(re); if (!m) return undefined;
    let n = parseFloat(m[1].replace(/\s/g, '').replace(',', '.')); if (!Number.isFinite(n)) return undefined;
    const u = m[2] || ''; if (/^тыс|^к$/.test(u)) n *= 1e3; else if (/^млн/.test(u)) n *= 1e6;
    return n;
  };
  const N = '(\\d[\\d\\s]*(?:[.,]\\d+)?)\\s*(тыс|млн|к\\b)?';
  const fields = {};
  const set = (k, v) => { if (v !== undefined) fields[k] = v; };
  set('price', find(new RegExp(`(?:цена|чек|стоит|чашк\\S*|за)\\D{0,18}?${N}`)));
  set('unit_cost', find(new RegExp(`себестоимост\\D{0,12}?${N}`)));
  set('units', find(new RegExp(`${N}\\s*(?:продаж|заказ|клиент|чаш|штук|шт)`)) ?? find(new RegExp(`(?:продаю|продаж)\\D{0,12}?${N}`)));
  set('fixed', find(new RegExp(`(?:аренд|реклам|расходы)\\D{0,14}?${N}`)));
  set('payroll', find(new RegExp(`(?:зарплат|фот)\\D{0,14}?${N}`)));
  set('investment', find(new RegExp(`(?:вложил|вложени|инвестиц|стартов)\\D{0,14}?${N}`)));
  if (/самозанят|физлиц|физическ/.test(t)) fields.client = 'person'; else if (/\bооо\b|юрлиц|компани/.test(t)) fields.client = 'company';
  return { fields };
}

// Возвращает undefined, если запрос не относится к инструментам.
export async function toolsHandle(key, body) {
  if (key === 'GET /api/tools') return { tools: [
    { id: 'finmodel', title: 'Финмодель', tagline: 'Маржа, налог, точка безубыточности и окупаемость за минуту' }] };
  if (key === 'GET /api/finmodel/meta') return { regimes: Object.entries(REGIMES).map(([value, label]) => ({ value, label })), profile_regime: 'usn_income', calculators: CALCS };
  if (key === 'POST /api/finmodel/calc') return calc(body || {});
  if (key === 'POST /api/finmodel/extract') {
    if (!String(body?.text || '').trim()) fail(400, 'empty', 'Опиши бизнес.');
    if (/сбой/.test(body.text)) fail(503, 'unavailable', 'Не удалось разобрать описание. Введи числа вручную.');
    await sleep(900); return extract(body.text);
  }
  return undefined;
}
