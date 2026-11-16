"""«Миф или правда»: five short statements per Moscow day with a plain explanation.

The deck is written by the team (general business and tax basics, no personal data, nothing from
paid courses). Every card may be backed by a free material of the knowledge base: it is looked
up by topic at run time, so only catalogue links are ever shown (D-04). Progress and streaks
live on the client.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import date

from aiohttp import web

from alpha_course.application.knowledge import Material
from alpha_course.webapp.api import Api, json_response
from alpha_course.webapp.serializers import material_json
from alpha_course.webapp.wordle import EPOCH, today_moscow

PER_DAY = 5


@dataclass(frozen=True)
class Myth:
    id: str
    statement: str
    truth: bool
    why: str
    topic: str  # search query for a free material


DECK: tuple[Myth, ...] = (
    Myth(
        "npd-staff",
        "Самозанятый на НПД может нанимать сотрудников по трудовому договору.",
        False,
        "На НПД нельзя иметь работников по трудовым договорам. "
        "Нужны сотрудники — выбирай ИП или ООО.",
        "самозанятый налог на профессиональный доход ограничения",
    ),
    Myth(
        "ip-liability",
        "Индивидуальный предприниматель отвечает по долгам своим личным имуществом.",
        True,
        "У ИП нет отдельного от человека имущества: по долгам бизнеса отвечает он сам, "
        "кроме имущества, которое закон защищает от взыскания.",
        "ответственность индивидуального предпринимателя по долгам",
    ),
    Myth(
        "ooo-liability",
        "Владелец ООО всегда отвечает по долгам компании всем личным имуществом.",
        False,
        "Обычно участник рискует только своей долей в компании. Исключения бывают, например "
        "при доведении компании до банкротства, но «всегда» — это миф.",
        "ответственность участников ооо",
    ),
    Myth(
        "ip-contributions",
        "Если у ИП нет дохода, страховые взносы за себя платить не нужно.",
        False,
        "Фиксированные страховые взносы ИП платит независимо от выручки, пока предприниматель "
        "не снят с учёта.",
        "страховые взносы ип за себя",
    ),
    Myth(
        "npd-kkt",
        "Самозанятому на НПД обязательно нужна онлайн-касса.",
        False,
        "Самозанятые формируют чек в приложении «Мой налог», кассовый аппарат им не нужен.",
        "самозанятые чек мой налог онлайн касса",
    ),
    Myth(
        "ip-account",
        "ИП обязан открыть расчётный счёт сразу при регистрации.",
        False,
        "Закон не требует от ИП расчётный счёт. Но с ним проще принимать оплату от компаний и "
        "отделять деньги бизнеса от личных.",
        "нужен ли расчётный счёт ип",
    ),
    Myth(
        "default-osno",
        "Если новый ИП не выбрал налоговый режим, он окажется на общей системе (ОСНО).",
        True,
        "Без заявления о переходе применяется общий режим — самый тяжёлый по отчётности. "
        "Выбери режим вовремя.",
        "выбор системы налогообложения ип осно усн",
    ),
    Myth(
        "revenue-profit",
        "Выручка — это то же самое, что прибыль.",
        False,
        "Выручка — все деньги от продаж. Прибыль остаётся после вычета расходов и налогов.",
        "выручка и прибыль разница",
    ),
    Myth(
        "margin-markup",
        "Маржа и наценка — одно и то же.",
        False,
        "Наценку считают от себестоимости, маржу — от цены. При наценке 100% маржа равна 50%.",
        "маржа наценка разница",
    ),
    Myth(
        "break-even",
        "Точка безубыточности — это объём продаж, при котором бизнес выходит в ноль, "
        "а не в прибыль.",
        True,
        "В этой точке выручка покрывает все расходы. Прибыль начинается только после неё.",
        "точка безубыточности",
    ),
    Myth(
        "turnover-profit",
        "Чем больше оборот, тем больше прибыль.",
        False,
        "Оборот может расти, а прибыль падать: скидки, реклама и аренда съедают разницу. "
        "Смотри на прибыль с каждой продажи.",
        "оборот и прибыль маржинальность",
    ),
    Myth(
        "cash-gap",
        "Прибыльный бизнес не может остаться без денег на счёте.",
        False,
        "Может: клиенты платят позже, чем нужно платить вам, — это кассовый разрыв. "
        "Прибыль на бумаге и деньги на счёте — разные вещи.",
        "кассовый разрыв",
    ),
    Myth(
        "business-plan",
        "Бизнес-план нужен только тем, кто просит инвестиции.",
        False,
        "План помогает и без инвесторов: он показывает, сколько нужно денег и когда придут "
        "первые продажи.",
        "бизнес-план для начинающего предпринимателя",
    ),
    Myth(
        "fixed-costs",
        "Постоянные расходы, например аренда, не зависят от числа продаж.",
        True,
        "Аренду и оклады платят независимо от выручки. Поэтому для старта важно знать, "
        "сколько продаж их покрывают.",
        "постоянные и переменные расходы",
    ),
    Myth(
        "oral-contract",
        "Любой договор между бизнесом и клиентом достаточно заключить устно.",
        False,
        "В ряде случаев закон требует письменной формы, например для сделок между компаниями. "
        "Письменный договор защищает и тебя, и клиента.",
        "договор с клиентом письменная форма",
    ),
    Myth(
        "trademark",
        "Название бренда защищено, как только ты начал им пользоваться.",
        False,
        "Исключительное право на товарный знак возникает после регистрации в Роспатенте, "
        "а не с первого использования.",
        "товарный знак регистрация защита бренда",
    ),
    Myth(
        "npd-tax",
        "Самозанятые не платят налог с доходов.",
        False,
        "Платят: 4% с доходов от физлиц и 6% с доходов от компаний и ИП. "
        "Налог считает приложение «Мой налог».",
        "налог на профессиональный доход ставки самозанятые",
    ),
    Myth(
        "mailing-consent",
        "Рекламную рассылку клиентам можно отправлять без их согласия.",
        False,
        "Для рекламных сообщений нужно согласие получателя. Без него можно получить жалобу "
        "и штраф, а клиенты — отписаться навсегда.",
        "рассылка реклама согласие клиента закон о рекламе",
    ),
    Myth(
        "cheap-client",
        "Если клиент пришёл дёшево, это всегда выгодно.",
        False,
        "Важно сравнивать стоимость привлечения с прибылью от клиента. Дешёвый клиент, "
        "который не вернётся, может принести убыток.",
        "стоимость привлечения клиента окупаемость",
    ),
    Myth(
        "usn-income",
        "На УСН «Доходы» налог считается с выручки, а не с прибыли.",
        True,
        "Расходы на размер налога не влияют. Поэтому «Доходы» удобны при низких расходах.",
        "усн доходы налог ставка",
    ),
    Myth(
        "discount",
        "Скидка всегда повышает прибыль.",
        False,
        "Скидка уменьшает маржу с каждой продажи. Продаж должно прибавиться заметно больше, "
        "иначе прибыль упадёт.",
        "скидки и прибыль ценообразование",
    ),
    Myth(
        "validate-demand",
        "Спрос на продукт стоит проверить на реальных покупателях до больших вложений.",
        True,
        "Несколько разговоров или пробных продаж показывают спрос дешевле и быстрее, "
        "чем запуск «на удачу».",
        "проверка бизнес-идеи спрос тестирование",
    ),
    Myth(
        "npd-companies",
        "Самозанятый может работать с компаниями, а не только с частными лицами.",
        True,
        "Может: тогда налог 6%, а компании нужен чек из «Мой налог». Есть и ограничения: "
        "например, нельзя перепродавать чужие товары.",
        "самозанятые работа с юридическими лицами",
    ),
    Myth(
        "zero-declaration",
        "Если у ИП на УСН не было доходов, декларацию сдавать не нужно.",
        False,
        "Декларацию по УСН сдают и при нулевых доходах: отчётность нужна, пока ИП на учёте.",
        "декларация усн ип нулевая отчётность",
    ),
    Myth(
        "mixed-money",
        "Смешивать личные и бизнес-деньги — нормально: учёту это не мешает.",
        False,
        "Когда деньги в одном кошельке, не видно, зарабатывает ли бизнес. "
        "Отделяй расходы бизнеса от личных с первого дня.",
        "разделение личных и бизнес финансов учёт",
    ),
)


# A material is shown only when its title mentions one of these stems: search alone sometimes
# returns a loosely related article, and a wrong link is worse than none.
MUST: dict[str, tuple[str, ...]] = {
    "npd-staff": ("самозан", "нпд"),
    "ip-liability": ("ответственность ип", "ответственность предпринимател"),
    "ooo-liability": ("ответственност", "участник"),
    "ip-contributions": ("взнос",),
    "npd-kkt": ("мой налог", "чек"),
    "ip-account": ("расчётн", "расчетн"),
    "default-osno": ("осно", "систем", "режим"),
    "revenue-profit": ("выручк",),
    "margin-markup": ("маржа", "маржин", "наценк"),
    "break-even": ("безубыточ",),
    "turnover-profit": ("прибыл", "маржин", "рентабельн"),
    "cash-gap": ("кассов", "денежн", "разрыв"),
    "business-plan": ("бизнес-план",),
    "fixed-costs": ("расход", "затрат"),
    "oral-contract": ("письменн", "устн", "форма договор"),
    "trademark": ("товарн",),
    "npd-tax": ("самозан", "нпд"),
    "mailing-consent": ("рассылк", "реклам"),
    "cheap-client": ("привлечени",),
    "usn-income": ("усн",),
    "discount": ("цен", "скидк"),
    "validate-demand": ("проверк", "спрос", "гипотез"),
    "npd-companies": ("самозан",),
    "zero-declaration": ("деклараци",),
    "mixed-money": ("личн", "раздел"),
}


def pick(day: date) -> list[Myth]:
    """Five cards for the day: a fixed shuffle, so a day never repeats a card and
    the whole deck is covered before it starts over."""
    order = sorted(DECK, key=lambda item: hashlib.sha256(item.id.encode()).hexdigest())
    start = ((day - EPOCH).days * PER_DAY) % len(order)
    return [order[(start + step) % len(order)] for step in range(PER_DAY)]


class MythsApi:
    def __init__(self, api: Api) -> None:
        self.api = api
        self._materials: dict[str, Material | None] = {}

    def _material(self, myth: Myth) -> Material | None:
        if myth.id not in self._materials:
            found = self.api.s.knowledge.search(myth.topic, candidates=10)
            stems = MUST.get(myth.id, ())
            self._materials[myth.id] = next(
                (
                    item
                    for item, _ in found.materials
                    if not item.is_paid
                    and item.has_safe_url
                    and item.kind != "glossary"
                    and any(stem in item.title.lower() for stem in stems)
                ),
                None,
            )
        return self._materials[myth.id]

    async def today(self, request: web.Request) -> web.Response:
        owner = self.api.owner(request)
        day = today_moscow()
        cards = []
        for myth in pick(day):
            material = await asyncio.to_thread(self._material, myth)
            cards.append(
                {
                    "id": myth.id,
                    "statement": myth.statement,
                    "truth": myth.truth,
                    "why": myth.why,
                    "material": material_json(material) if material else None,
                }
            )
        await self.api.log(owner, "myths_opened", day=str(day))
        return json_response({"date": day.isoformat(), "cards": cards})


def register(app: web.Application, api: Api) -> None:
    myths = MythsApi(api)
    app.router.add_get("/api/myths", myths.today)
