"""Telegram HTML rendering for consultation answers, materials and plans."""

from __future__ import annotations

from html import escape

from alpha_course.application.assistant import Recommendation, Reply
from alpha_course.application.knowledge import Material
from alpha_course.application.offers import NOTE, describe
from alpha_course.application.profile import BusinessProfile

ACCESS_BADGES = {"free": "бесплатно", "paid": "платный курс", "subscription": "Подписка Курс+"}


def offer_html(pitch: dict[str, str]) -> str:
    """A/B variant B: a personal «next step» block for one relevant paid course."""
    lines = [
        "🎯 <b>Следующий шаг для тебя</b>",
        f"💎 <b>{escape(pitch['title'])}</b> · {escape(pitch['price'])}",
    ]
    if pitch["benefit"]:
        lines.append(escape(pitch["benefit"]))
    lines.append(f"<i>{escape(pitch['fit'])} {escape(pitch['note'])}</i>")
    return "\n".join(lines)


def material_line(
    material: Material, why: str = "", *, numbered: int | None = None, link: bool = True
) -> str:
    prefix = f"{numbered}. " if numbered is not None else ""
    badge = "💎" if material.is_paid else material.icon
    meta = [material.kind_label]
    if material.access == "subscription":
        meta.append(ACCESS_BADGES["subscription"])
    elif material.is_paid:
        meta.append(material.price or ACCESS_BADGES[material.access])
    else:
        meta.append("бесплатно")
        if material.duration and material.kind == "article":
            meta.append(material.duration)
        if material.lessons and material.kind == "course":
            meta.append(f"{material.lessons} уроков")
    if link:
        title = f'<a href="{escape(material.url, quote=True)}">{escape(material.title)}</a>'
    else:
        title = f"<b>{escape(material.title)}</b>"
    line = f"{prefix}{badge} {title}"
    line += f"\n<i>{escape(' · '.join(meta))}</i>"
    if material.is_paid and not why:
        # Public card only (D-04): annotation/topic and the page link note, no program.
        why = f"{describe(material)}. {NOTE}"
    if why:
        line += f" — {escape(why)}"
    return line


def answer_html(reply: Reply, *, profile_note: str = "") -> str:
    parts: list[str] = []
    if reply.status == "local":
        if reply.excerpt:
            source = reply.sources[0].title if reply.sources else ""
            parts.append(f"💡 <b>Нашёл в базе знаний</b>\n{escape(reply.excerpt)}")
            if source:
                parts.append(f"<i>Из материала «{escape(source)}»</i>")
        else:
            parts.append("🤔 В базе знаний не нашёл ответа на этот вопрос.")
    elif reply.status == "unavailable":
        parts.append(f"⚠️ <b>Не удалось собрать ответ</b>\n{escape(reply.summary)}")
    elif reply.status == "no_evidence":
        parts.append(f"🤔 <b>Уточни вопрос: точного ответа в базе нет</b>\n{escape(reply.summary)}")
    else:
        parts.append(f"💡 <b>Коротко</b>\n{escape(reply.summary)}")
    if reply.steps:
        lines = "\n".join(f"{index}. {escape(step)}" for index, step in enumerate(reply.steps, 1))
        parts.append(f"🪜 <b>Что сделать</b>\n{lines}")
    if reply.personal:
        parts.append(f"👤 <b>Для тебя</b>\n{escape(reply.personal)}")
    if reply.recommendations:
        title = (
            "📚 <b>Изучить подробнее</b>"
            if reply.status != "no_evidence"
            else "📚 <b>Близкие материалы</b>"
        )
        # Paid courses open through the counted «Подробнее о курсе» button, not an inline link.
        lines = "\n\n".join(
            material_line(rec.material, rec.why, link=not rec.material.is_paid)
            for rec in reply.recommendations
        )
        parts.append(f"{title}\n{lines}")
    if reply.sources and reply.status in ("answered", "partial"):
        names = ", ".join(f"«{escape(item.title)}»" for item in reply.sources)
        parts.append(f"<i>Источник: {names}</i>")
    if profile_note:
        parts.append(profile_note)
    return "\n\n".join(parts)


def profile_note(updates: dict) -> str:
    if not updates:
        return ""
    labels = BusinessProfile().merged(**updates).filled()
    if not labels:
        return ""
    text = ", ".join(labels.values())
    return f"📝 <i>Запомнил о бизнесе: {escape(text)}. Изменить — в «👤 Профиль».</i>"


def plan_html(intro: str, steps: list[Recommendation], done: set[int] | None = None) -> str:
    done = done or set()
    lines = "\n\n".join(
        ("✅ " if index - 1 in done else "") + material_line(rec.material, rec.why, numbered=index)
        for index, rec in enumerate(steps, 1)
    )
    progress = f" · пройдено {len(done)} из {len(steps)}" if done else ""
    return f"🧭 <b>Твой маршрут обучения</b>{progress}\n{escape(intro)}\n\n{lines}"


def profile_html(profile: BusinessProfile, status: str = "") -> str:
    filled = profile.filled()
    from alpha_course.application.profile import FIELD_LABELS

    rows = "\n".join(
        f"{label}: <b>{escape(filled[name])}</b>"
        if name in filled
        else f"{label}: <i>не указано</i>"
        for name, label in FIELD_LABELS.items()
    )
    head = "👤 <b>Профиль бизнеса</b>"
    hint = (
        "\n\n<i>Профиль необязателен, но с ним советы точнее, а налоговый календарь — под твой режим.</i>"
        if len(filled) < 3
        else ""
    )
    return f"{head}{chr(10) + status if status else ''}\n\n{rows}{hint}"
