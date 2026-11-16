"""Business tools: financial model (docs/webapp-api.md)."""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

import aiohttp
from aiohttp import web

from alpha_course.application.finmodel import (
    PROFILE_REGIMES,
    REGIMES,
    FinmodelError,
    calculate,
    parse_inputs,
)
from alpha_course.application.privacy import mask_personal
from alpha_course.webapp import pdf
from alpha_course.webapp.api import Api, ApiError, bad, json_response, read_body, text_field
from alpha_course.webapp.serializers import material_json

_LOG = logging.getLogger(__name__)

TOOLS = (
    {
        "id": "finmodel",
        "title": "Финмодель",
        "tagline": "Маржа, налог, точка безубыточности и окупаемость за минуту",
    },
)

EXTRACT = """Из описания бизнеса достань числа для финансовой модели. Верни СТРОГО JSON:
{"price": число|null, "unit_cost": число|null, "units": число|null, "fixed": число|null,
 "payroll": число|null, "investment": число|null, "client": "person"|"company"|null}
- price — цена (средний чек) за одну продажу, ₽; unit_cost — переменные затраты на одну продажу
  (себестоимость), ₽; units — сколько продаж в месяц; fixed — постоянные расходы в месяц
  (аренда, реклама, связь), ₽; payroll — зарплаты в месяц, ₽; investment — стартовые вложения, ₽.
- Числа в рублях, «50 тыс» = 50000, «1,2 млн» = 1200000. Если числа нет в тексте — null.
  Ничего не выдумывай и не оценивай сам. Никакого текста вне JSON."""

CALCULATOR_QUERY = "калькулятор налог зарплата"

TELEGRAM_API = "https://api.telegram.org"
BOT_TOKEN = web.AppKey("bot_token", str)
SEND_DOCUMENT = web.AppKey("send_document")


async def send_document(token: str, chat_id: int, data: bytes, filename: str, caption: str) -> bool:
    form = aiohttp.FormData()
    form.add_field("chat_id", str(chat_id))
    form.add_field("caption", caption)
    form.add_field("document", data, filename=filename, content_type="application/pdf")
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(f"{TELEGRAM_API}/bot{token}/sendDocument", data=form) as response:
            return response.status == 200


class ToolsApi:
    def __init__(self, api: Api) -> None:
        self.api = api
        services = api.s
        self.model = getattr(services.assistant, "model", None) or services.model
        self.answer_model = getattr(services.assistant, "answer_model", "deepseek-v4.1-flash")

    async def tools(self, request: web.Request) -> web.Response:
        self.api.owner(request)
        return json_response({"tools": list(TOOLS)})

    # ------------------------------------------------------------------ finmodel
    async def finmodel_meta(self, request: web.Request) -> web.Response:
        owner = self.api.owner(request)
        profile = await self.api.s.profiles.load(owner)
        return json_response(
            {
                "regimes": [{"value": key, "label": label} for key, label in REGIMES.items()],
                "profile_regime": PROFILE_REGIMES.get(profile.tax_regime or ""),
                "calculators": [
                    material_json(item)
                    for item in self.api.s.knowledge.materials.values()
                    if item.kind == "calculator" and not item.is_paid
                ][:6],
            }
        )

    async def finmodel_calc(self, request: web.Request) -> web.Response:
        owner = self.api.owner(request)
        body = await read_body(request)
        try:
            result = calculate(parse_inputs(body))
        except FinmodelError as error:
            raise bad(str(error), "invalid_input") from None
        await self.api.log(owner, "finmodel_calculated", regime=result.inputs.regime)
        return json_response(result.public())

    async def finmodel_extract(self, request: web.Request) -> web.Response:
        owner = self.api.owner(request)
        body = await read_body(request)
        text = text_field(body, "text", limit=1200)
        if self.model is None:
            raise ApiError(503, "unavailable", "ИИ-помощник сейчас недоступен.")
        raw = None
        for _ in range(2):
            try:
                raw = await self.model.complete(
                    EXTRACT,
                    json.dumps({"text": mask_personal(text, limit=1200)}, ensure_ascii=False),
                    model=self.answer_model,
                    json_mode=True,
                    temperature=0,
                    max_tokens=1500,
                )
                if raw and raw.strip():
                    break
            except Exception as error:
                _LOG.warning("finmodel_extract_failed error=%s", type(error).__name__)
        try:
            data = json.loads(raw) if raw else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise ApiError(
                503, "unavailable", "Не удалось разобрать описание. Введи числа вручную."
            )
        fields: dict[str, float | str] = {}
        for key in ("price", "unit_cost", "units", "fixed", "payroll", "investment"):
            value = data.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                fields[key] = value
        if data.get("client") in ("person", "company"):
            fields["client"] = data["client"]
        await self.api.log(owner, "finmodel_extracted", fields=len(fields))
        return json_response({"fields": fields})

    # ------------------------------------------------------------------ pdf
    async def deliver(
        self, request: web.Request, body: dict, data: bytes, name: str, caption: str, owner: int
    ) -> web.Response:
        filename = f"{name}.pdf"
        if body.get("send") is True:
            token = request.app.get(BOT_TOKEN)
            sender = request.app.get(SEND_DOCUMENT) or send_document
            if not token:
                raise ApiError(503, "unavailable", "Отправка в чат сейчас недоступна.")
            try:
                ok = await sender(token, owner, data, filename, caption)
            except Exception as error:
                _LOG.warning("pdf_send_failed error=%s", type(error).__name__)
                ok = False
            if not ok:
                raise ApiError(502, "send_failed", "Не получилось отправить файл в чат с ботом.")
            await self.api.log(owner, "pdf_sent", name=name)
            return json_response({"sent": True, "filename": filename})
        await self.api.log(owner, "pdf_downloaded", name=name)
        return web.Response(
            body=data,
            content_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )

    async def finmodel_pdf(self, request: web.Request) -> web.Response:
        owner = self.api.owner(request)
        body = await read_body(request)
        try:
            result = calculate(parse_inputs(body))
        except FinmodelError as error:
            raise bad(str(error), "invalid_input") from None
        try:
            data = await pdf.render_pdf(pdf.report_html(result))
        except pdf.PdfUnavailable:
            raise ApiError(503, "unavailable", "PDF сейчас недоступен.") from None
        return await self.deliver(
            request, body, data, "Финмодель", "Финансовая модель: отчёт по расчёту", owner
        )


def register(app: web.Application, api: Api) -> None:
    tools = ToolsApi(api)
    app.router.add_get("/api/tools", tools.tools)
    app.router.add_get("/api/finmodel/meta", tools.finmodel_meta)
    app.router.add_post("/api/finmodel/calc", tools.finmodel_calc)
    app.router.add_post("/api/finmodel/extract", tools.finmodel_extract)
    app.router.add_post("/api/finmodel/pdf", tools.finmodel_pdf)
