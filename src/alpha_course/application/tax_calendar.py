"""Налоговый календарь для напоминаний о бизнес-событиях (T2-06, T2-08).

Справочные сроки по НК РФ на 2026 год; перед уплатой сверяйтесь с nalog.ru.

Модуль чистый и детерминированный: без ввода-вывода и сети. Сроки приходят
из формы бизнеса и режима налогообложения; если данных не хватает, возвращается
пустой список, а интерфейс сам уточняет недостающее (`missing_for_calendar`).

Пропущено намеренно (нет уверенности без даты начала или деталей):
- ПСН: сроки зависят от даты начала и срока патента, датированных пунктов нет;
- налог на прибыль ООО на ОСНО, отдельные сроки НДФЛ за сотрудников (23-е число и позже);
- региональные и имущественные налоги.
Годовые 6-НДФЛ и РСВ по закону сдаются 1 марта и 25 января, а не 25 февраля.
"""

from dataclasses import dataclass
from datetime import date, timedelta

USN = ("УСН «Доходы»", "УСН «Доходы минус расходы»")
_MONTHS = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)
_QUARTER_NAMES = {1: "1 квартал", 2: "полугодие", 3: "9 месяцев", 4: "год"}

# Праздники-переносы 2026 года (помимо фиксированных дат и переносов с выходных).
_EXTRA_HOLIDAYS = frozenset({date(2026, 1, 9), date(2026, 12, 31)})
_FIXED = ((2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4))


@dataclass(frozen=True)
class TaxDeadline:
    due: date
    title: str
    what: str
    kind: str  # "payment" | "report" | "notice"
    code: str
    raw: date | None = None  # дата по закону до переноса на рабочий день

    @property
    def shifted(self) -> bool:
        return self.raw is not None and self.raw != self.due


def _holidays(year: int) -> frozenset[date]:
    result = {date(year, 1, day) for day in range(1, 9)}
    for month, day in _FIXED:
        holiday = date(year, month, day)
        # Праздник на выходной переносится на ближайший рабочий понедельник.
        if holiday.weekday() == 5:
            holiday += timedelta(days=2)
        elif holiday.weekday() == 6:
            holiday += timedelta(days=1)
        result.add(holiday)
    return frozenset(result)


def _is_working(day: date) -> bool:
    if day.weekday() >= 5 or day in _EXTRA_HOLIDAYS:
        return False
    return day not in _holidays(day.year)


def _shift(day: date) -> date:
    while not _is_working(day):
        day += timedelta(days=1)
    return day


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _item(raw: date, title: str, what: str, kind: str, code: str) -> TaxDeadline:
    return TaxDeadline(due=_shift(raw), title=title, what=what, kind=kind, code=code, raw=raw)


_PAY = "Заплати {} через единый налоговый платёж (ЕНП)."
_NOTICE = "Подай в налоговую уведомление о сумме {}, чтобы платёж зачли."


def _usn(year: int, legal_form: str | None) -> list[TaxDeadline]:
    items = []
    for quarter, month in ((1, 4), (2, 7), (3, 10)):
        name = _QUARTER_NAMES[quarter]
        items.append(
            _item(
                date(year, month, 25),
                f"Уведомление по авансу УСН за {name}",
                _NOTICE.format("аванса по УСН"),
                "notice",
                f"usn_notice_q{quarter}_{year}",
            )
        )
        items.append(
            _item(
                date(year, month, 28),
                f"Аванс по УСН за {name}",
                _PAY.format("аванс по УСН"),
                "payment",
                f"usn_advance_q{quarter}_{year}",
            )
        )
    if legal_form in ("ИП", "ООО"):
        month = 4 if legal_form == "ИП" else 3
        items.append(
            _item(
                date(year, month, 25),
                f"Декларация по УСН за {year - 1} год",
                "Сдай годовую декларацию по УСН в налоговую.",
                "report",
                f"usn_declaration_{year}",
            )
        )
        items.append(
            _item(
                date(year, month, 28),
                f"Налог по УСН за {year - 1} год",
                _PAY.format("налог по УСН за год"),
                "payment",
                f"usn_annual_tax_{year}",
            )
        )
    return items


def _ip_contributions(year: int) -> list[TaxDeadline]:
    return [
        _item(
            date(year, 12, 28),
            "Фиксированные взносы ИП за себя",
            _PAY.format("фиксированные страховые взносы за себя"),
            "payment",
            f"ip_fixed_contrib_{year}",
        ),
        _item(
            date(year, 7, 1),
            "Взнос 1% с дохода свыше 300 000 ₽",
            "Заплати 1% с дохода выше 300 000 ₽ за прошлый год, если он был больше.",
            "payment",
            f"ip_contrib_1pct_{year}",
        ),
    ]


def _osno_ip(year: int) -> list[TaxDeadline]:
    items = [
        _item(
            date(year, 4, 30),
            f"Декларация 3-НДФЛ за {year - 1} год",
            "Сдай декларацию 3-НДФЛ о доходах за прошлый год.",
            "report",
            f"osno_3ndfl_{year}",
        ),
        _item(
            date(year, 7, 15),
            f"НДФЛ ИП за {year - 1} год",
            "Заплати НДФЛ за прошлый год по декларации 3-НДФЛ.",
            "payment",
            f"osno_ndfl_annual_{year}",
        ),
    ]
    for quarter, month in ((1, 4), (2, 7), (3, 10)):
        name = _QUARTER_NAMES[quarter]
        items.append(
            _item(
                date(year, month, 25),
                f"Уведомление по авансу НДФЛ за {name}",
                _NOTICE.format("аванса по НДФЛ"),
                "notice",
                f"osno_ndfl_notice_q{quarter}_{year}",
            )
        )
        items.append(
            _item(
                date(year, month, 28),
                f"Аванс по НДФЛ за {name}",
                _PAY.format("аванс по НДФЛ"),
                "payment",
                f"osno_ndfl_advance_q{quarter}_{year}",
            )
        )
    return items


def _vat(year: int) -> list[TaxDeadline]:
    """НДС за кварталы, закончившиеся в прошлом и текущем году, сроки в `year`."""
    items = []
    for quarter in (1, 2, 3, 4):
        first_month = quarter * 3 + 1  # месяц после квартала
        if first_month > 12:
            first_month -= 12  # срок за 4 кв. прошлого года лежит в январе-марте
        label = f"{quarter} квартал"
        quarter_year = year if quarter < 4 else year - 1
        items.append(
            _item(
                date(year, first_month, 25),
                f"Декларация по НДС за {label} {quarter_year}",
                "Сдай декларацию по НДС в налоговую.",
                "report",
                f"vat_declaration_q{quarter}_{quarter_year}",
            )
        )
        for part in range(3):
            month = first_month + part
            items.append(
                _item(
                    date(year, month, 28),
                    f"НДС за {label} {quarter_year}, часть {part + 1} из 3",
                    _PAY.format("треть НДС"),
                    "payment",
                    f"vat_pay_q{quarter}_{quarter_year}_{part + 1}",
                )
            )
    return items


def _monthly(
    year: int,
    month: int,
    day: int,
    prefix: str,
    title: str,
    what: str,
    kind: str,
) -> TaxDeadline:
    period_year, period_month = _prev_month(year, month)
    return _item(
        date(year, month, day),
        f"{title} за {_MONTHS[period_month - 1]}",
        what,
        kind,
        f"{prefix}_{period_year}_{period_month:02d}",
    )


def _employees(year: int) -> list[TaxDeadline]:
    items = []
    for month in range(1, 13):
        items.append(
            _monthly(
                year,
                month,
                25,
                "emp_notice",
                "Уведомление по НДФЛ и взносам за сотрудников",
                _NOTICE.format("НДФЛ и взносов за сотрудников"),
                "notice",
            )
        )
        items.append(
            _monthly(
                year,
                month,
                28,
                "emp_pay",
                "НДФЛ и взносы за сотрудников",
                _PAY.format("НДФЛ и страховые взносы за сотрудников"),
                "payment",
            )
        )
        items.append(
            _monthly(
                year,
                month,
                25,
                "efs1",
                "ЕФС-1 с данными о сотрудниках",
                "Отправь в СФР отчёт ЕФС-1 с персонифицированными сведениями.",
                "report",
            )
        )
    for quarter, month in ((1, 4), (2, 7), (3, 10)):
        name = _QUARTER_NAMES[quarter]
        items.append(
            _item(
                date(year, month, 25),
                f"6-НДФЛ за {name}",
                "Сдай расчёт 6-НДФЛ по сотрудникам в налоговую.",
                "report",
                f"emp_6ndfl_q{quarter}_{year}",
            )
        )
        items.append(
            _item(
                date(year, month, 25),
                f"РСВ за {name}",
                "Сдай расчёт по страховым взносам (РСВ) в налоговую.",
                "report",
                f"emp_rsv_q{quarter}_{year}",
            )
        )
    items.append(
        _item(
            date(year, 1, 25),
            f"РСВ за {year - 1} год",
            "Сдай годовой расчёт по страховым взносам (РСВ) в налоговую.",
            "report",
            f"emp_rsv_q4_{year - 1}",
        )
    )
    items.append(
        _item(
            date(year, 3, 1),
            f"6-НДФЛ за {year - 1} год",
            "Сдай годовой расчёт 6-НДФЛ по сотрудникам в налоговую.",
            "report",
            f"emp_6ndfl_q4_{year - 1}",
        )
    )
    return items


def _for_year(
    year: int, legal_form: str | None, tax_regime: str, employees: bool | None
) -> list[TaxDeadline]:
    items: list[TaxDeadline] = []
    if tax_regime == "НПД":
        for month in range(1, 13):
            items.append(
                _monthly(
                    year,
                    month,
                    28,
                    "npd",
                    "Налог НПД",
                    "Заплати налог самозанятого: сумма есть в приложении «Мой налог».",
                    "payment",
                )
            )
        return items
    if tax_regime == "АУСН":
        for month in range(1, 13):
            items.append(
                _monthly(
                    year,
                    month,
                    25,
                    "aucn",
                    "Налог по АУСН",
                    "Оплати налог по АУСН: сумму посчитает банк-партнёр, проверь её.",
                    "payment",
                )
            )
    elif tax_regime in USN:
        items += _usn(year, legal_form)
    elif tax_regime == "ОСНО" and legal_form == "ИП":
        items += _osno_ip(year)
    elif tax_regime == "ОСНО" and legal_form == "ООО":
        items += _vat(year)
    if legal_form == "ИП" and tax_regime != "АУСН":
        items += _ip_contributions(year)
    if employees and legal_form in ("ИП", "ООО"):
        items += _employees(year)
    return items


def tax_deadlines(
    legal_form: str | None,
    tax_regime: str | None,
    employees: bool | None,
    *,
    today: date,
    horizon_days: int = 365,
) -> list[TaxDeadline]:
    """Ближайшие сроки в окне [today, today + horizon_days], по возрастанию даты."""
    if tax_regime in (None, "", "не знаю") or legal_form == "пока не зарегистрирован":
        return []
    end = today + timedelta(days=horizon_days)
    found: dict[str, TaxDeadline] = {}
    for year in range(today.year - 1, end.year + 2):
        for item in _for_year(year, legal_form, tax_regime, employees):
            if today <= item.due <= end:
                found.setdefault(item.code, item)
    return sorted(found.values(), key=lambda item: (item.due, item.code))


def missing_for_calendar(legal_form: str | None, tax_regime: str | None) -> list[str]:
    """Поля профиля, которые надо спросить до построения календаря."""
    missing = []
    if legal_form in (None, ""):
        missing.append("legal_form")
    if tax_regime in (None, "", "не знаю"):
        missing.append("tax_regime")
    return missing


_EXPLAINERS = {
    "НПД": (
        "Самозанятые платят налог раз в месяц до 28-го числа за прошлый месяц. "
        "Декларацию подавать не нужно, сумму покажет приложение «Мой налог»."
    ),
    "УСН «Доходы»": (
        "Налог считается от доходов: авансы платят раз в квартал, итог за год "
        "и декларация — весной следующего года."
    ),
    "УСН «Доходы минус расходы»": (
        "Налог считается с разницы между доходами и расходами: авансы платят "
        "раз в квартал, итог за год и декларация — весной следующего года."
    ),
    "ПСН": (
        "Патент оплачивается один раз или частями — сроки зависят от даты начала "
        "и срока патента, они указаны в самом патенте."
    ),
    "АУСН": (
        "Налог по АУСН платят раз в месяц до 25-го числа за прошлый месяц, "
        "декларацию сдавать не нужно."
    ),
    "ОСНО": (
        "Общая система самая сложная: у ИП это НДФЛ и декларация 3-НДФЛ, "
        "плюс НДС каждый квартал. Лучше сверяться с бухгалтером."
    ),
}


def regime_explainer(tax_regime: str | None) -> str:
    if tax_regime in _EXPLAINERS:
        return _EXPLAINERS[tax_regime]
    return (
        "Режим налогообложения указан в уведомлении при регистрации или в личном "
        "кабинете на nalog.ru. Когда узнаешь его, я составлю календарь сроков."
    )
