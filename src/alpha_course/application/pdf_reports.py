"""Output format requested explicitly by the user."""

import re


def is_pdf_request(text: str) -> bool:
    return bool(re.search(r"\bpdf\b|пдф", text, re.I))


def is_pdf_export(text: str) -> bool:
    """Only short format requests may reuse a previous successful AI answer."""
    normalized = re.sub(r"[.,!?]", "", text.lower()).strip()
    return bool(re.fullmatch(
        r"(?:(?:пришли|отправь|сохрани|сделай|оформи|дай|экспортируй)\s+)?"
        r"(?:мне\s+)?"
        r"(?:(?:это|этот ответ|ответ|его|отчёт|отчет|результат)\s+)?"
        r"(?:(?:в(?:\s+формате)?|как)\s+)?(?:pdf|пдф)(?:\s+пожалуйста)?",
        normalized,
    ))
