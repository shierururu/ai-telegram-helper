"""Explicit PDF output request detection."""

import pytest

from alpha_course.application.pdf_reports import is_pdf_export, is_pdf_request


@pytest.mark.parametrize("text", ["Сделай PDF", "отчёт в пдф", "PDF по маркетингу"])
def test_pdf_request(text):
    assert is_pdf_request(text)


def test_regular_task_does_not_request_document():
    assert not is_pdf_request("Подготовь план запуска бизнеса")


@pytest.mark.parametrize("text", [
    "пришли мне это в формате PDF",
    "сохрани ответ в формате пдф",
    "отправь результат в PDF",
    "сделай в формате PDF",
    "Пришли это в PDF, пожалуйста!",
])
def test_export_only_phrases(text):
    assert is_pdf_export(text)


@pytest.mark.parametrize("text", [
    "сделай план запуска в формате PDF",
    "пришли мне расчёт налогов в PDF",
    "отправь результат анализа рынка в PDF",
    "сохрани ответ и добавь новые рекомендации в формате PDF",
])
def test_substantive_pdf_tasks_require_new_classification(text):
    assert not is_pdf_export(text)
