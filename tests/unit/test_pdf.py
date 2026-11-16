"""PDF design: HTML is escaped and complete, endpoints deliver or send, renderer is optional."""

from __future__ import annotations

import inspect
import re
from datetime import date
from pathlib import Path

import pytest

from alpha_course.application.finmodel import calculate, count_text, parse_inputs
from alpha_course.webapp import pdf, tools
from tests.unit.test_webapp_games import make_rig

BLOCKS = [
    {"heading": "", "text": "г. Омск\nДата: 01.12.2026"},
    {"heading": "Предмет <b>договора</b>", "text": "Оплата: ________.\n\nВторой абзац & т.д."},
    {"heading": "Реквизиты и подписи сторон", "text": "Заказчик ____________________"},
]


def test_document_html_escapes_and_marks_blanks():
    markup = pdf.document_html("Договор <script>", BLOCKS, today=date(2026, 11, 11))
    assert "<script>" not in markup and "&lt;script&gt;" in markup
    assert "&lt;b&gt;договора&lt;/b&gt;" in markup
    assert markup.count('class="blank"') == 2
    assert 'class="sec lead"' in markup and 'class="sec sign"' in markup
    assert markup.count("<h2>") == 2  # the lead block has no heading
    assert "11 ноября 2026" in markup and "черновик" not in markup.lower() and "ИИ" not in markup
    assert "Второй абзац &amp; т.д." in markup


def test_report_html_has_numbers_from_code_and_one_page_class():
    result = calculate(
        parse_inputs(
            {"price": 300, "unit_cost": 100, "units": 500, "fixed": 40000, "investment": 800000}
        )
    )
    markup = pdf.report_html(result, today=date(2026, 11, 11))
    assert "51 000 ₽" in markup and "220 шт." in markup and "15,7 мес." in markup
    assert 'class="page-report"' in markup and "Модель сходится" in markup
    assert "выгоднее всего" in markup and "ваш выбор" in markup


def test_report_handles_loss_and_no_break_even():
    loss = calculate(parse_inputs({"price": 100, "unit_cost": 150, "units": 10, "fixed": 1000}))
    markup = pdf.report_html(loss)
    assert "Точки безубыточности нет" in markup and "Работа в минус" in markup
    assert 'class="num loss"' in markup


def test_money_uses_non_breaking_spaces():
    assert pdf.money(1234567.4) == "1 234 567 ₽"


NBSP, MINUS = chr(0x00A0), chr(0x2212)
REPORT_CASES = (
    {"price": 2500, "unit_cost": 900, "units": 400, "fixed": 120000, "payroll": 150000},
    {"price": 1500, "unit_cost": 300, "units": 180, "payroll": 30000, "regime": "npd"},
    {"price": 1000, "unit_cost": 700, "units": 100, "fixed": 90000, "regime": "usn_income_expense"},
    {"price": 1500000, "unit_cost": 400000, "units": 2500000, "fixed": 50000000, "investment": 9e8},
    {"price": 990.5, "unit_cost": 120, "units": 12.5, "fixed": 30000, "rate": "7,5"},
    {"price": 100, "unit_cost": 150, "units": 10, "fixed": 1000},
)


@pytest.mark.parametrize("body", REPORT_CASES)
def test_report_html_draws_only_glyphs_the_pdf_fonts_have(body):
    markup = pdf.report_html(calculate(parse_inputs(body)), today=date(2026, 11, 13))
    unsafe = {char for char in markup if not pdf._pdf_safe(char)}
    assert not unsafe, [f"U+{ord(char):04X}" for char in unsafe]
    text = re.sub(r"<[^>]+>", " ", markup.split("<body", 1)[1])
    assert not re.search(r"\de[+-]\d", text)  # no «2.5e+06 шт.»
    assert not re.search(r"\d\.\d", text)  # decimals use a comma
    assert not re.search(r"-\d", text)  # negative sums use a real minus sign


def test_money_price_percent_and_counts_read_the_russian_way():
    assert pdf.money(-126000) == MINUS + "126" + NBSP + "000" + NBSP + "₽"
    assert pdf.money(-0.4) == "0" + NBSP + "₽"
    assert pdf.price(990.5) == "990,50" + NBSP + "₽" and pdf.price(2500) == pdf.money(2500)
    assert pdf.percent(7.5) == "7,5%" and pdf.percent(6.000000000000001) == "6%"
    assert pdf.percent(-50) == MINUS + "50%" and pdf.percent(-0.01) == "0%"
    assert count_text(2_500_000) == "2" + NBSP + "500" + NBSP + "000"
    assert count_text(12.5) == "12,5" and count_text(25.25) == "25,25" and count_text(3.0) == "3"


def test_long_figures_get_a_smaller_size_instead_of_overflowing():
    huge = calculate(parse_inputs(REPORT_CASES[3]))
    markup = pdf.report_html(huge)
    assert 'class="num ok fit-s"' in markup and 'class="v fit-s"' in markup
    assert "меньше 0,1 мес." in markup and "Налоговый режим" not in markup
    small = pdf.report_html(calculate(parse_inputs(REPORT_CASES[0])))
    assert "fit-" not in small.split("<body", 1)[1]


def test_pdf_text_drops_emoji_and_maps_special_spaces():
    narrow = chr(0x202F)
    raw = f"📊 Итог:  5{narrow}000 ₽ ✅\n🚀 Дальше ⇒ план 👍🏽!\nСемья 👨‍👩‍👧 ок"
    clean = pdf.pdf_text(raw)
    assert clean == f"Итог: 5{NBSP}000 ₽\nДальше → план!\nСемья ок"
    assert all(pdf._pdf_safe(char) for char in clean)
    assert pdf.pdf_text("УСН «Доходы» — 6 % · №1 ≈ 2 ≤ 3") == "УСН «Доходы» — 6 % · №1 ≈ 2 ≤ 3"
    assert pdf.esc("<b>🔥</b>") == "&lt;b&gt;&lt;/b&gt;"


def test_documents_and_ai_answers_lose_emoji_too():
    markup = pdf.document_html("📄 Ответ", [{"heading": "✅ Шаги", "text": "1️⃣ Открыть ИП"}])
    assert "Ответ</h1>" in markup and "<h2>Шаги</h2>" in markup and "<p>1 Открыть ИП</p>" in markup


def test_print_css_has_a_rouble_capable_fallback_in_every_font_stack():
    stacks = re.findall(r"\"Liberation (?:Sans|Serif)\"[^;}]*", pdf.CSS)
    assert stacks and all("DejaVu" in stack for stack in stacks)
    assert "fonts-dejavu-core" in (Path(__file__).parents[2] / "Dockerfile").read_text("utf-8")


async def test_render_requires_a_pdf_backend(monkeypatch):
    monkeypatch.setattr(pdf, "_weasyprint", lambda markup: None)

    async def none(markup):
        return None

    monkeypatch.setattr(pdf, "_chrome", none)
    with pytest.raises(pdf.PdfUnavailable):
        await pdf.render_pdf("<p>x</p>")


async def test_real_render_when_a_backend_exists():
    if pdf._find_chrome() is None:
        try:
            from weasyprint import HTML  # noqa: F401
        except Exception:
            pytest.skip("no PDF renderer on this machine")
    data = await pdf.render_pdf(pdf.document_html("Тест", BLOCKS))
    assert data.startswith(b"%PDF") and len(data) > 2000


@pytest.fixture
def fake_render(monkeypatch):
    async def render(markup):
        return b"%PDF-1.4 fake " + markup[:20].encode()

    monkeypatch.setattr(pdf, "render_pdf", render)


async def test_removed_document_endpoints_are_gone(fake_render):
    rig = await make_rig(None)
    async with rig:
        for path in ("/api/documents/pdf", "/api/documents/draft"):
            status, _ = await rig.call("POST", path, {"title": "x", "blocks": BLOCKS})
            assert status in (404, 405), path


async def test_pdf_sent_to_the_chat_with_the_bot(fake_render):
    rig = await make_rig(None)
    sent = []

    async def sender(token, chat_id, data, filename, caption):
        sent.append((chat_id, filename, caption, data[:4]))
        return True

    rig.app[tools.SEND_DOCUMENT] = sender
    async with rig:
        status, body = await rig.call(
            "POST",
            "/api/finmodel/pdf",
            {"price": 300, "unit_cost": 100, "units": 500, "fixed": 40000, "send": True},
        )
    assert status == 200 and body == {"sent": True, "filename": "Финмодель.pdf"}
    assert sent and sent[0][0] == 4242 and sent[0][3] == b"%PDF"


async def test_pdf_send_failure_and_bad_numbers(fake_render):
    rig = await make_rig(None)

    async def failing(*args):
        return False

    rig.app[tools.SEND_DOCUMENT] = failing
    async with rig:
        status, body = await rig.call(
            "POST", "/api/finmodel/pdf", {"price": 300, "units": 5, "send": True}
        )
        assert status == 502 and body["error"] == "send_failed"
        status, body = await rig.call("POST", "/api/finmodel/pdf", {"price": 0, "units": 5})
        assert status == 400 and body["error"] == "invalid_input"


async def test_pdf_unavailable_gives_friendly_503(monkeypatch):
    async def boom(markup):
        raise pdf.PdfUnavailable("none")

    monkeypatch.setattr(pdf, "render_pdf", boom)
    rig = await make_rig(None)
    async with rig:
        status, body = await rig.call("POST", "/api/finmodel/pdf", {"price": 300, "units": 5})
    assert status == 503 and "PDF" in body["message"]


def test_send_document_is_async():
    assert inspect.iscoroutinefunction(tools.send_document)


def test_report_break_even_edge_cases_are_readable():
    free = calculate(parse_inputs({"price": 300, "unit_cost": 100, "units": 50, "fixed": 0}))
    markup = pdf.report_html(free)
    assert "В плюсе с первой продажи" in markup and "0 шт." not in markup
    idle = calculate(parse_inputs({"price": 300, "unit_cost": 100, "units": 0, "fixed": 40000}))
    markup = pdf.report_html(idle)
    assert "Продаж в плане нет" in markup and 'class="fill" style="width:0.0%"' in markup
    short = calculate(parse_inputs({"price": 300, "unit_cost": 100, "units": 100, "fixed": 40000}))
    markup = pdf.report_html(short)
    assert "не хватает: нужно ещё" in markup
