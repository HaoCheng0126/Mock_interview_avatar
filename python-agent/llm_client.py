"""Shared async LLM client for DeepSeek via OpenAI-compatible API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from time import perf_counter

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


def _error_text(exc: BaseException) -> str:
    return str(exc).strip() or exc.__class__.__name__


class LlmClient:
    """Async LLM client. Supports both streaming and non-streaming generation."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        system_prompt: str = "",
    ) -> None:
        self._api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self._base_url = base_url or os.getenv(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        )
        self._model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self._system_prompt = system_prompt
        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            _enforce_credentials=bool(self._api_key),
        )
        self._messages: list[dict] = [{"role": "system", "content": system_prompt}]
        self._lock = asyncio.Lock()

    async def generate(self, user_text: str, max_tokens: int = 512) -> str:
        """Non-streaming generation. Returns full reply."""
        self._messages.append({"role": "user", "content": user_text})
        if len(self._messages) > 21:
            self._messages = [self._messages[0]] + self._messages[-20:]

        full_reply = ""
        for attempt in range(2):
            try:
                stream = await self._client.chat.completions.create(
                    model=self._model,
                    messages=self._messages,
                    stream=True,
                    max_tokens=max_tokens,
                    temperature=0.7,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        full_reply += delta.content
                break
            except Exception as e:
                logger.warning(
                    "LLM API error (attempt %d/2): %s", attempt + 1, e
                )
                if attempt == 0:
                    await asyncio.sleep(1)
                else:
                    full_reply = "抱歉，我暂时无法回答，请稍后再试。"

        if full_reply.strip():
            self._messages.append({"role": "assistant", "content": full_reply})
        return full_reply

    async def generate_once(self, user_text: str, max_tokens: int = 512) -> str:
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_text},
        ]
        full_reply = ""
        for attempt in range(2):
            try:
                stream = await self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    stream=True,
                    max_tokens=max_tokens,
                    temperature=0.7,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        full_reply += delta.content
                break
            except Exception as e:
                logger.warning(
                    "LLM API error (attempt %d/2): %s", attempt + 1, e
                )
                if attempt == 0:
                    await asyncio.sleep(1)
                else:
                    full_reply = "抱歉，我暂时无法回答，请稍后再试。"
        return full_reply

    async def generate_json_once(
        self,
        user_text: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str:
        """Generate one complete JSON response and raise when generation fails.

        Report generation must not turn transport errors into a natural-language
        apology: that text cannot be parsed and previously caused a silent fallback.
        Prefer provider JSON mode, then retry once without it for OpenAI-compatible
        providers that do not implement ``response_format``.
        """
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_text},
        ]
        last_error: Exception | None = None
        for attempt in range(2):
            started = perf_counter()
            kwargs = {
                "model": self._model,
                "messages": messages,
                "stream": False,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if attempt == 0:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                completion = await self._client.chat.completions.create(**kwargs)
                choice = completion.choices[0]
                content = str(choice.message.content or "").strip()
                if not content:
                    raise ValueError("LLM returned an empty JSON response")
                finish_reason = str(choice.finish_reason or "")
                if finish_reason == "length":
                    raise ValueError(
                        f"LLM JSON output was truncated at {max_tokens} tokens"
                    )
                # Fail here with a useful error instead of making every caller
                # rediscover that the provider returned prose/markdown.
                candidate = content
                if candidate.startswith("```"):
                    candidate = candidate.split("\n", 1)[-1]
                    candidate = candidate.rsplit("```", 1)[0].strip()
                json.loads(candidate)
                logger.info(
                    "LLM JSON request succeeded (model=%s, json_mode=%s, prompt_chars=%s, elapsed=%.2fs)",
                    self._model,
                    attempt == 0,
                    len(user_text),
                    perf_counter() - started,
                )
                return candidate
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "LLM JSON API error (attempt %d/2, model=%s, json_mode=%s, prompt_chars=%s, elapsed=%.2fs): %s",
                    attempt + 1,
                    self._model,
                    attempt == 0,
                    len(user_text),
                    perf_counter() - started,
                    _error_text(exc),
                )
                # Retrying without JSON mode only helps providers that reject the
                # response_format parameter. A completed but truncated/invalid
                # response will fail the same way again and used to consume the
                # entire report timeout.
                if isinstance(exc, ValueError):
                    raise RuntimeError(
                        f"LLM JSON generation failed: {exc}"
                    ) from exc
                if attempt == 0:
                    await asyncio.sleep(0.4)
        detail = _error_text(last_error or RuntimeError("unknown error"))
        raise RuntimeError(f"LLM JSON generation failed: {detail}") from last_error

    async def generate_streaming(
        self, user_text: str, on_chunk, max_tokens: int = 512
    ) -> str:
        """Streaming generation. Calls on_chunk(text_delta) for each fragment."""
        async with self._lock:
            self._messages.append({"role": "user", "content": user_text})
            if len(self._messages) > 21:
                self._messages = [self._messages[0]] + self._messages[-20:]

            full_reply = ""
            for attempt in range(2):
                try:
                    stream = await self._client.chat.completions.create(
                        model=self._model,
                        messages=self._messages,
                        stream=True,
                        max_tokens=max_tokens,
                        temperature=0.7,
                    )
                    async for chunk in stream:
                        delta = chunk.choices[0].delta
                        if delta.content:
                            full_reply += delta.content
                            await on_chunk(delta.content)
                    break
                except Exception as e:
                    logger.warning(
                        "LLM streaming error (attempt %d/2): %s", attempt + 1, e
                    )
                    if attempt == 0:
                        await asyncio.sleep(1)
                    else:
                        full_reply = "抱歉，我暂时无法回答，请稍后再试。"
                        await on_chunk(full_reply)

            if full_reply.strip():
                self._messages.append({"role": "assistant", "content": full_reply})
            return full_reply

    def reset_context(self) -> None:
        self._messages = [{"role": "system", "content": self._system_prompt}]
