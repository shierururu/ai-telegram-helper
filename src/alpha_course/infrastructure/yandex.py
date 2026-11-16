"""Async transport for Yandex AI Studio: chat models and text embeddings.

The OpenAI-compatible endpoint is used. Callers are responsible for passing only
public knowledge-base fragments and masked user text (see application.privacy).

Latency: deepseek-v4.1-flash is a reasoning model. Its hidden reasoning tokens count
against ``max_tokens``; with reasoning on, the consultation composer spent the whole
2000-token budget on reasoning (``finish_reason=length``, empty content) and took
16–24 s per attempt. ``reasoning_effort="none"`` (accepted by the endpoint) disables it,
so JSON answers arrive in a few seconds and ``max_tokens`` bounds the visible output.
"""

import asyncio
import logging
import random
import time

from aiohttp import ClientError, ClientSession, ClientTimeout

logger = logging.getLogger(__name__)

REASONING_EFFORTS = ("none", "low", "medium", "high", "default")

_API_URL = "https://ai.api.cloud.yandex.net/v1"
_MODEL_NAME = "deepseek-v4.1-flash"
FAST_MODEL = "deepseek-v4.1-flash"
ANSWER_MODEL = "deepseek-v4.1-flash"
EMBED_DOC = "text-embeddings-v2-doc"
EMBED_QUERY = "text-embeddings-v2-query"


class YandexError(RuntimeError):
    """Provider failure with diagnostics safe to log."""


class YandexAI:
    def __init__(
        self,
        api_key: str,
        *,
        folder_id: str | None = None,
        timeout_seconds: float = 20.0,
        model: str = _MODEL_NAME,
        reasoning_effort: str = "none",
    ) -> None:
        if reasoning_effort not in REASONING_EFFORTS:
            raise ValueError("Unsupported reasoning effort")
        self._api_key = api_key.strip()
        self._folder = (folder_id or "").strip() or None
        self._timeout_seconds = timeout_seconds
        self._model = model
        # "default" leaves the provider's own reasoning behaviour untouched.
        self._reasoning = None if reasoning_effort == "default" else reasoning_effort
        self._folder_lock = asyncio.Lock()
        self._embed_limit = asyncio.Semaphore(4)
        self._session: ClientSession | None = None

    async def _client(self) -> ClientSession:
        if self._session is None or self._session.closed:
            self._session = ClientSession(
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=ClientTimeout(total=self._timeout_seconds),
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _folder_id(self, session: ClientSession) -> str:
        async with self._folder_lock:
            if self._folder is not None:
                return self._folder
            async with session.get(f"{_API_URL}/models", allow_redirects=False) as response:
                if response.status != 200:
                    raise YandexError(f"Yandex model discovery failed (HTTP {response.status})")
                data = await response.json()
            for item in data.get("data", []):
                model_id = item.get("id")
                if isinstance(model_id, str) and "://" in model_id:
                    folder = model_id.split("://", 1)[1].split("/", 1)[0]
                    if folder:
                        self._folder = folder
                        return folder
            raise YandexError("No Yandex models are available for this key")

    async def complete(
        self,
        instruction: str,
        payload: str,
        *,
        model: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.0,
        max_tokens: int = 700,
    ) -> str:
        """Send an already sanitized payload and return the assistant message content.

        Only fast provider refusals (429/5xx) are retried, with short jittered pauses;
        a timed-out call is not repeated, so one call waits at most ``timeout_seconds``
        plus the pauses. Callers bound the whole question with their own deadline.
        """
        for attempt in range(3):
            try:
                return await self._complete_once(
                    instruction, payload, model, json_mode, temperature, max_tokens
                )
            except _Retry:
                if attempt < 2:
                    await asyncio.sleep(0.6 * (attempt + 1) + random.random() * 0.4)
        raise YandexError("Yandex completion rate-limited")

    async def _complete_once(
        self,
        instruction: str,
        payload: str,
        model: str | None,
        json_mode: bool,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if not self._api_key:
            raise YandexError("Missing Yandex API key")
        started = time.monotonic()
        name = model or self._model
        try:
            session = await self._client()
            folder = await self._folder_id(session)
            request = {
                "model": f"gpt://{folder}/{name}/latest",
                "messages": [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": payload},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if self._reasoning is not None:
                request["reasoning_effort"] = self._reasoning
            if json_mode:
                request["response_format"] = {"type": "json_object"}
            async with session.post(
                f"{_API_URL}/chat/completions", json=request, allow_redirects=False
            ) as response:
                if response.status == 429 or response.status >= 500:
                    raise _Retry()
                if response.status != 200:
                    raise YandexError(f"Yandex completion failed (HTTP {response.status})")
                data = await response.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except TimeoutError:
            raise YandexError("Yandex request timed out") from None
        except ClientError:
            raise YandexError("Yandex request failed") from None
        except KeyError, IndexError, TypeError, ValueError:
            raise YandexError("Invalid response from Yandex") from None
        _log_usage(name, started, max_tokens, data, choice)
        if not isinstance(content, str) or not content.strip():
            raise YandexError("Yandex returned an empty completion")
        return content.strip()

    async def web_answer(
        self, instruction: str, payload: str, *, model: str | None = None, max_tokens: int = 3500
    ) -> tuple[str, list[dict[str, str]]]:
        """Answer with Yandex's built-in web search; returns the text and cited pages.

        Only an already masked payload may be passed: the query leaves the service.
        """
        if not self._api_key:
            raise YandexError("Missing Yandex API key")
        for attempt in range(2):
            try:
                session = await self._client()
                folder = await self._folder_id(session)
                request = {
                    "model": f"gpt://{folder}/{model or self._model}/latest",
                    "instructions": instruction,
                    "input": payload,
                    "tools": [{"type": "web_search"}],
                    "max_output_tokens": max_tokens,
                }
                async with session.post(
                    f"{_API_URL}/responses",
                    json=request,
                    allow_redirects=False,
                    timeout=ClientTimeout(total=max(self._timeout_seconds, 170.0)),
                ) as response:
                    if response.status == 429 or response.status >= 500:
                        raise _Retry()
                    if response.status != 200:
                        raise YandexError(f"Yandex search failed (HTTP {response.status})")
                    data = await response.json()
                return _parse_web(data)
            except _Retry:
                await asyncio.sleep(1 + attempt)
            except TimeoutError:
                raise YandexError("Yandex request timed out") from None
            except ClientError:
                raise YandexError("Yandex request failed") from None
            except KeyError, IndexError, TypeError, ValueError:
                raise YandexError("Invalid response from Yandex") from None
        raise YandexError("Yandex search rate-limited")

    async def embed(self, text: str, *, query: bool = False, attempts: int = 6) -> list[float]:
        """Embed one public text; retries rate limits with jittered backoff."""
        if not self._api_key:
            raise YandexError("Missing Yandex API key")
        name = EMBED_QUERY if query else EMBED_DOC
        delay = 0.5
        for attempt in range(attempts):
            try:
                async with self._embed_limit:
                    session = await self._client()
                    folder = await self._folder_id(session)
                    body = {"model": f"emb://{folder}/{name}/latest", "input": text[:6000]}
                    async with session.post(f"{_API_URL}/embeddings", json=body) as response:
                        if response.status == 429 or response.status >= 500:
                            raise _Retry()
                        if response.status != 200:
                            raise YandexError(f"Yandex embedding failed (HTTP {response.status})")
                        data = await response.json()
                vector = data["data"][0]["embedding"]
                if not isinstance(vector, list) or not vector:
                    raise YandexError("Invalid embedding")
                return [float(value) for value in vector]
            except _Retry, TimeoutError, ClientError:
                if attempt == attempts - 1:
                    break
                await asyncio.sleep(delay + random.random() * delay)
                delay = min(delay * 2, 8)
            except KeyError, IndexError, TypeError, ValueError:
                raise YandexError("Invalid response from Yandex") from None
        raise YandexError("Yandex embedding unavailable")


class _Retry(Exception):
    pass


def _log_usage(model: str, started: float, max_tokens: int, data: object, choice: object) -> None:
    """Timing and token counts only: never the prompt or the answer text."""
    usage = data.get("usage") if isinstance(data, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("completion_tokens_details")
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
    finish = choice.get("finish_reason") if isinstance(choice, dict) else None
    logger.info(
        "llm_call model=%s ms=%d prompt_tokens=%s completion_tokens=%s reasoning_tokens=%s "
        "max_tokens=%s finish=%s",
        model,
        (time.monotonic() - started) * 1000,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        reasoning,
        max_tokens,
        finish,
    )


# Backwards-compatible name used by earlier entrypoints and tests.
YandexDeepSeek = YandexAI


def _parse_web(data: dict) -> tuple[str, list[dict[str, str]]]:
    """Collect the final message text and url citations from a Responses API result."""
    texts: list[str] = []
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
            for note in part.get("annotations") or []:
                url = note.get("url")
                if note.get("type") != "url_citation" or not isinstance(url, str):
                    continue
                if not url.startswith(("https://", "http://")) or url in seen:
                    continue
                seen.add(url)
                title = note.get("title") if isinstance(note.get("title"), str) else ""
                links.append({"title": title.strip()[:120] or url, "url": url})
    answer = "\n".join(texts).strip()
    if not answer:
        raise YandexError("Yandex returned an empty completion")
    return answer, links[:6]
