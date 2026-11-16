"""PDF for an AI answer: the same A4 design as documents and the financial report."""

import re


def plain(text: str) -> str:
    """Drop markdown marks the model may leave in: the PDF shows plain paragraphs."""
    text = re.sub(r"\*\*|__|`", "", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    return re.sub(r"(?m)^\s*[-*]\s+", "• ", text)


async def render_report(
    title: str, paragraphs: list[str], *, links: list[dict[str, str]] | None = None
) -> bytes:
    from alpha_course.webapp.pdf import (  # lazy: avoids a webapp<->telegram import cycle
        document_html,
        render_pdf,
    )

    text = "\n\n".join(plain(item).strip() for item in paragraphs if item.strip())
    blocks = [{"heading": "", "text": text}]
    if links:
        listing = "\n".join(f"{item['title']} — {item['url']}" for item in links[:6])
        blocks.append({"heading": "Источники", "text": listing})
    return await render_pdf(document_html(title, blocks))
