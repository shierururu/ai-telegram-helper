# ruff: noqa: E501  (HTML templates read better on one line)
"""PDF for documents and the financial model: one HTML/CSS design, two interchangeable renderers.

WeasyPrint is the production renderer (the Docker image has its libraries); where those are not
installed (a Windows dev machine) headless Chrome or Edge renders the same HTML.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import shutil
import subprocess
import tempfile
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

from alpha_course.application.finmodel import REGIMES, Inputs, Result, count_text

logger = logging.getLogger(__name__)
CSS = (Path(__file__).parent / "print.css").read_text(encoding="utf-8")
BLANK = re.compile(r"_{4,}")
MONTHS = (
    "января февраля марта апреля мая июня июля августа сентября октября ноября декабря"
).split()
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
)
VERDICTS = {
    "ok": ("Модель сходится", "ok"),
    "tight": ("Запас прочности небольшой", "tight"),
    "loss": ("Работа в минус", "loss"),
}


NBSP = chr(0x00A0)
MINUS = chr(0x2212)
RUB = chr(0x20BD)
# Characters outside these ranges are not guaranteed in the PDF fonts (Liberation in Docker with
# DejaVu as the fallback, Arial/Times locally) and come out as empty boxes («квадратики»).
PDF_SAFE_RANGES = (
    (0x0020, 0x007E),  # ASCII
    (0x00A0, 0x00FF),  # Latin-1: «», middle dot, no-break space
    (0x0401, 0x045F),  # Russian Cyrillic
    (0x2010, 0x2014),  # hyphens and dashes
    (0x2018, 0x201E),  # quotes
    (0x2020, 0x2022),  # daggers and the bullet
    (0x2026, 0x2026),  # ellipsis
    (0x2030, 0x2030),  # per mille
    (0x20AC, 0x20AC),  # euro
    (0x20BD, 0x20BD),  # rouble
    (0x2116, 0x2116),  # numero
    (0x2190, 0x2195),  # arrows
    (0x2212, 0x2212),  # minus
    (0x2248, 0x2248),  # almost equal
    (0x2260, 0x2265),  # not equal, less/greater or equal
)
PDF_REPLACE = {
    chr(0x2002): " ",  # en space
    chr(0x2003): " ",  # em space
    chr(0x2009): " ",  # thin space
    chr(0x200A): " ",  # hair space
    chr(0x2007): NBSP,  # figure space
    chr(0x202F): NBSP,  # narrow no-break space (number grouping in Python/JS locales)
    chr(0x2011): "-",  # non-breaking hyphen
    chr(0x2015): chr(0x2014),  # horizontal bar
    chr(0x2027): chr(0x00B7),  # hyphenation point
    chr(0x2219): chr(0x00B7),  # bullet operator
    chr(0x25CF): chr(0x2022),  # black circle
    chr(0x25AA): chr(0x2022),  # small black square
    chr(0x21D2): chr(0x2192),  # double arrow
    chr(0x27A1): chr(0x2192),  # emoji arrow
    chr(0x2794): chr(0x2192),  # heavy arrow
    chr(0x2713): "+",  # check mark
    chr(0x2714): "+",  # heavy check mark
    chr(0x2716): chr(0x00D7),  # heavy multiplication x
    "\t": " ",
}
_SPACE_BEFORE_PUNCT = re.compile(r" +([,.;:!?)])")
_SPACE_RUNS = re.compile(r" {2,}")


def _pdf_safe(char: str) -> bool:
    code = ord(char)
    return char in "\n\r" or any(low <= code <= high for low, high in PDF_SAFE_RANGES)


def pdf_text(value: Any) -> str:
    """Text the PDF fonts can draw: special spaces and arrows mapped; emoji and other glyphs the
    fonts lack (they print as empty boxes) dropped together with the gap they leave behind."""
    text = "".join(PDF_REPLACE.get(char, char) for char in unicodedata.normalize("NFC", str(value)))
    if all(_pdf_safe(char) for char in text):
        return text
    kept = "".join(char for char in text if _pdf_safe(char))
    kept = _SPACE_RUNS.sub(" ", _SPACE_BEFORE_PUNCT.sub(r"\1", kept))
    return "\n".join(line.strip() for line in kept.split("\n"))


class PdfUnavailable(RuntimeError):
    pass


def esc(value: Any) -> str:
    return html.escape(pdf_text(value), quote=True)


def price(value: float) -> str:
    """A unit price keeps its kopecks when it has them: «990,50 ₽», but «2 500 ₽»."""
    if abs(value - round(value)) < 0.005:
        return money(value)
    sign = MINUS if value < 0 else ""
    return sign + f"{abs(value):,.2f}".replace(",", NBSP).replace(".", ",") + NBSP + RUB


def percent(value: float) -> str:
    """«7,5%», «6%», «64,0%» is «64%»: comma decimals without a trailing zero."""
    text = f"{abs(value):.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return (MINUS if round(value, 1) < 0 else "") + text + "%"


def fit(text: str, medium: int, small: int) -> str:
    """A class that shrinks a figure too long for its box: billions must not overflow it."""
    if len(text) > small:
        return " fit-s"
    if len(text) > medium:
        return " fit-m"
    return ""


def money(value: float) -> str:
    """Whole roubles with no-break group spaces and a real minus sign: «−126 000 ₽»."""
    amount = int(round(value))
    sign = MINUS if amount < 0 else ""
    return sign + f"{abs(amount):,}".replace(",", NBSP) + NBSP + RUB


def today_text(today: date | None = None) -> str:
    today = today or date.today()
    return f"{today.day} {MONTHS[today.month - 1]} {today.year}"


def page(body: str, title: str, cls: str = "") -> str:
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        f'<title>{esc(title)}</title><style>{CSS}</style></head><body class="{cls}">{body}</body></html>'
    )


def mast(tag: str) -> str:
    return (
        '<header class="mast"><span class="brand"><i></i>Бизнес-навигатор</span>'
        f'<span class="tag">{esc(tag)}</span></header>'
    )


def text_html(text: str) -> str:
    """Escape, keep line breaks and turn runs of underscores into fill-in lines."""
    paragraphs = [item for item in re.split(r"\n\s*\n", text.strip()) if item.strip()]
    out = []
    for paragraph in paragraphs or [text]:
        line = esc(paragraph.strip()).replace("\n", "<br>")
        out.append("<p>" + BLANK.sub('<span class="blank"></span>', line) + "</p>")
    return "".join(out)


def document_html(title: str, blocks: list[dict[str, str]], *, today: date | None = None) -> str:
    sections = []
    for index, block in enumerate(blocks):
        heading = (block.get("heading") or "").strip()
        text = block.get("text") or ""
        last = index == len(blocks) - 1
        classes = ["sec"]
        if not heading and index == 0:
            classes.append("lead")
        if last and re.search(r"подпис|реквизит", heading, re.I):
            classes.append("sign")
        head = f"<h2>{esc(heading)}</h2>" if heading else ""
        sections.append(f'<section class="{" ".join(classes)}">{head}{text_html(text)}</section>')
    body = (
        mast("Документ")
        + f"<h1>{esc(title)}</h1>"
        + f'<p class="doc-sub">Сформировано {today_text(today)}</p>'
        + f'<div class="doc">{"".join(sections)}</div>'
    )
    return page(body, title)


def _kpi(label: str, value: str, sub: str = "") -> str:
    extra = f'<div class="s">{esc(sub)}</div>' if sub else ""
    return (
        f'<div class="kpi"><div class="k">{esc(label)}</div>'
        f'<div class="v{fit(value, 11, 15)}">{esc(value)}</div>{extra}</div>'
    )


def _payback(months: float) -> str:
    if months < 0.05:
        return "меньше 0,1 мес."
    return f"{months:.1f}".replace(".", ",") + " мес."


def report_html(result: Result, *, today: date | None = None) -> str:
    inputs: Inputs = result.inputs
    verdict_text, verdict_class = VERDICTS.get(result.verdict, VERDICTS["ok"])
    expenses = result.variable + result.fixed_total
    kpis = "".join(
        (
            _kpi(
                "Выручка",
                money(result.revenue),
                f"{count_text(inputs.units)} продаж по {price(inputs.price)}",
            ),
            _kpi("Расходы", money(expenses), "без налогов"),
            _kpi("Налоги", money(result.tax), f"ставка {percent(result.tax_rate * 100)}"),
            _kpi(
                "Маржа",
                percent(result.margin_pct),
                f"{price(result.unit_margin)} с продажи",
            ),
        )
    )

    payback = (
        _payback(result.payback_months)
        if result.payback_months is not None
        else "не окупается"
        if inputs.investment > 0
        else "вложений нет"
    )
    payback_cell = f'<div><div class="k">Окупаемость</div><div class="v">{esc(payback)}</div></div>'
    if result.break_even_units is None:
        even = (
            '<p class="bar-note">Точки безубыточности нет: цена не выше переменных затрат.</p>'
            f'<div class="even">{payback_cell}</div>'
        )
    elif result.break_even_units == 0:
        even = (
            '<p class="bar-note">В плюсе с первой продажи: постоянные расходы покрываются '
            "без выручки.</p>"
            f'<div class="even">{payback_cell}</div>'
        )
    else:
        planned = inputs.units
        top = max(planned, result.break_even_units) * 1.15
        fill = min(100.0, planned / top * 100)
        mark = result.break_even_units / top * 100
        if planned <= 0:
            safety = "Продаж в плане нет: до точки безубыточности нужно набрать продажи."
        elif result.safety_pct is not None and result.safety_pct > 0:
            safety = (
                f"Запас прочности {result.safety_pct:.0f}%: продажи могут упасть на столько, "
                "и вы всё равно не уйдёте в минус."
            )
        else:
            short = result.break_even_units - planned
            safety = (
                f"Плановых продаж не хватает: нужно ещё {count_text(short)} шт. "
                "до точки безубыточности."
            )
        even = (
            '<div class="even">'
            f'<div><div class="k">Нужно продаж</div><div class="v">{esc(count_text(result.break_even_units))} шт.</div></div>'
            f'<div><div class="k">Нужна выручка</div><div class="v">{esc(money(result.break_even_revenue or 0))}</div></div>'
            f'<div><div class="k">План</div><div class="v">{esc(count_text(inputs.units))} шт.</div></div>'
            f"{payback_cell}</div>"
            f'<div class="bar"><div class="fill" style="width:{fill:.1f}%"></div>'
            f'<div class="mark" style="left:{mark:.1f}%"></div></div>'
            f'<p class="bar-note">{esc(safety)}</p>'
        )

    def profit_cell(value: int) -> str:
        return f'<td class="r {"pos" if value >= 0 else "neg"}">{money(value)}</td>'

    scenarios = "".join(
        f'<tr><td>{esc(item["label"])}</td><td class="r">{money(item["revenue"])}</td>'
        f"{profit_cell(item['profit'])}</tr>"
        for item in result.scenarios
    )
    regimes = []
    for item in result.regimes:
        pills = ""
        if item.get("best"):
            pills += '<span class="pill">выгоднее всего</span>'
        if item["current"]:
            pills += '<span class="pill cur">ваш выбор</span>'
        name = esc(item["label"]) + ("<small>" + pills + "</small>" if pills else "")
        if not item["available"]:
            regimes.append(
                f'<tr><td class="na">{esc(item["label"])}</td><td class="r na">не подходит</td>'
                '<td class="r na">—</td></tr>'
            )
            continue
        regimes.append(
            f'<tr><td class="{"best" if item.get("best") else ""}">{name}</td>'
            f'<td class="r">{money(item["tax"])}</td>{profit_cell(item["profit"])}</tr>'
        )
    given = (
        ("Цена одной продажи", price(inputs.price)),
        ("Переменные затраты на продажу", price(inputs.unit_cost)),
        ("Продаж в месяц", count_text(inputs.units)),
        ("Постоянные расходы", money(inputs.fixed)),
        ("Зарплаты", money(inputs.payroll)),
        ("Стартовые вложения", money(inputs.investment)),
    )
    # The regime is not a row here: «УСН «Доходы минус расходы»» in a narrow number column broke
    # into four lines and squeezed the labels; the subtitle and the «ваш выбор» pill name it.
    given_rows = "".join(
        f'<tr><td>{esc(label)}</td><td class="r">{esc(value)}</td></tr>' for label, value in given
    )
    warnings = ""
    if result.warnings:
        warnings = (
            '<div class="warn">'
            + "".join(f"<p>{esc(item)}</p>" for item in result.warnings)
            + "</div>"
        )

    body = (
        mast("Финмодель")
        + '<div class="rep">'
        + '<h1 class="rep-title">Финансовая модель</h1>'
        + f'<p class="rep-sub">{esc(REGIMES[inputs.regime])} · расчёт на {today_text(today)}</p>'
        + '<div class="hero"><div><div class="lbl">Чистая прибыль в месяц</div>'
        + f'<div class="num {verdict_class}{fit(money(result.profit), 13, 17)}">'
        + f"{esc(money(result.profit))}</div></div>"
        + f'<span class="chip {verdict_class}">{esc(verdict_text)}</span></div>'
        + f'<div class="kpis">{kpis}</div>'
        + f"<h3>Точка безубыточности</h3>{even}"
        + '<h3>Что если</h3><table><tr><th>Сценарий</th><th class="r">Выручка</th>'
        + f'<th class="r">Прибыль в месяц</th></tr>{scenarios}</table>'
        + '<div class="two"><div><h3>Налоговые режимы</h3><table><tr><th>Режим</th>'
        + '<th class="r">Налог</th><th class="r">Прибыль</th></tr>'
        + f"{''.join(regimes)}</table></div><div><h3>Исходные данные</h3>"
        + f"<table>{given_rows}</table></div></div>"
        + warnings
        + '<p class="foot">Все суммы указаны за месяц. Ставки по умолчанию: '
        + "в вашем регионе они могут отличаться; страховые взносы с зарплат учтены в размере 30%. "
        + "Расчёт ориентировочный, для решений проверьте его с бухгалтером.</p></div>"
    )
    return page(body, "Финансовая модель", "page-report")


# ---------------------------------------------------------------------------- rendering
def _find_chrome() -> str | None:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _weasyprint(markup: str) -> bytes | None:
    try:
        from weasyprint import HTML  # type: ignore[import-not-found]
    except Exception:  # missing native libraries raise OSError, not ImportError
        return None
    return HTML(string=markup).write_pdf()


def _chrome_sync(markup: str) -> bytes | None:
    chrome = _find_chrome()
    if chrome is None:
        return None
    with tempfile.TemporaryDirectory(prefix="pdf-") as folder:
        source = Path(folder) / "in.html"
        target = Path(folder) / "out.pdf"
        source.write_text(markup, encoding="utf-8")
        command = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--user-data-dir={Path(folder) / 'profile'}",
            f"--print-to-pdf={target}",
            source.as_uri(),
        ]
        try:
            subprocess.run(command, timeout=40, check=False, capture_output=True)
        except subprocess.TimeoutExpired, OSError:
            return None
        return target.read_bytes() if target.is_file() else None


async def _chrome(markup: str) -> bytes | None:
    # Not asyncio subprocesses: the Windows selector loop used with psycopg does not support them.
    return await asyncio.to_thread(_chrome_sync, markup)


async def render_pdf(markup: str) -> bytes:
    data = await asyncio.to_thread(_weasyprint, markup)
    if data is None:
        data = await _chrome(markup)
    if not data or not data.startswith(b"%PDF"):
        raise PdfUnavailable("No PDF renderer is available")
    return data
