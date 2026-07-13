"""Shared async LLM client for DeepSeek via OpenAI-compatible API."""

from __future__ import annotations

import asyncio
import logging
import os

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


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
