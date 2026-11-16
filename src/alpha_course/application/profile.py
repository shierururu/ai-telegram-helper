"""Business profile shared by consultation, tax calendar and Telegram adapters.

Every field is optional: the assistant must stay useful with an empty profile (B-05).
Values are short generic categories, never free-form personal text, so they can be
passed to the external model as personalization context (D-02).
"""

from dataclasses import asdict, dataclass, fields, replace

STAGES = ("идея", "запуск", "работает до года", "развитие")
INDUSTRIES = (
    "розница",
    "общепит",
    "услуги",
    "онлайн-торговля и маркетплейсы",
    "производство",
    "строительство",
    "логистика",
    "IT и digital",
    "образование",
    "красота и здоровье",
    "оптовая торговля",
    "другое",
)
LEGAL_FORMS = ("самозанятый", "ИП", "ООО", "пока не зарегистрирован")
TAX_REGIMES = (
    "НПД",
    "УСН «Доходы»",
    "УСН «Доходы минус расходы»",
    "ПСН",
    "АУСН",
    "ОСНО",
    "не знаю",
)
LEVELS = ("novice", "experienced")
LEVEL_LABELS = {"novice": "новичок", "experienced": "есть опыт"}
FIELD_LABELS = {
    "stage": "Этап",
    "industry": "Сфера",
    "goal": "Задача",
    "legal_form": "Форма",
    "tax_regime": "Налоги",
    "employees": "Сотрудники",
    "level": "Опыт",
}


@dataclass(frozen=True)
class BusinessProfile:
    stage: str | None = None
    industry: str | None = None
    goal: str | None = None
    legal_form: str | None = None
    tax_regime: str | None = None
    employees: bool | None = None
    level: str | None = None

    def is_empty(self) -> bool:
        return all(getattr(self, item.name) in (None, "") for item in fields(self))

    def filled(self) -> dict[str, str]:
        """Human-readable non-empty fields, in display order."""
        result = {}
        for name in FIELD_LABELS:
            value = getattr(self, name)
            if value in (None, ""):
                continue
            if name == "employees":
                value = "есть" if value else "нет"
            elif name == "level":
                value = LEVEL_LABELS.get(value, value)
            result[name] = str(value)
        return result

    def context(self) -> dict[str, str]:
        """Generic categories that may be sent to the external model."""
        return {FIELD_LABELS[name]: value for name, value in self.filled().items()}

    def merged(self, **changes: object) -> BusinessProfile:
        clean = {key: value for key, value in changes.items() if key in FIELD_LABELS}
        return replace(self, **clean)

    def as_dict(self) -> dict:
        return asdict(self)
