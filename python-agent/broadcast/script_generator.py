"""LLM-driven product script generation."""

from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_TEMPLATE_ZH = """你是一个专业的电商直播带货主播。请根据以下商品信息，生成8段口播脚本。
每段脚本80-200字，适合口播，语气热情有感染力。

分段要求（每段覆盖不同角度）：
1. 开场吸引 — 引起兴趣，点出痛点
2. 产品卖点 — 核心成分或设计亮点
3. 使用效果 — 实际体验感受
4. 对比优势 — 和同类产品/市场价对比
5. 使用场景 — 什么时候用、怎么用
6. 价格优惠 — 今天直播间的专属福利
7. 社交证明 — 好评、销量、回购率
8. 限时催单 — 库存紧张、倒计时、行动号召

要求：
- 用口语化中文，多用感叹词（姐妹们、家人们、真的太...了）
- 杜绝AI痕迹，要像真人主播在说话
- 每段脚本应该足够长，能支撑10-15秒的口播

商品信息：
{product_info}

请只返回JSON数组格式：["脚本1", "脚本2", ...]"""

DEFAULT_PROMPT_TEMPLATE_EN = """You are a live shopping host on TikTok. Generate 8 short narration scripts based on the product info below.
Each script should be 15-30 words, spoken naturally with high energy.

Each segment covers a different angle:
1. Hook — grab attention, call out a relatable pain point
2. Product highlights — key features, what makes it special
3. Personal results — real experience using it, specific details
4. Comparison — vs competitors or retail price, why this one wins
5. Use cases — when/how to use it in daily life
6. Flash deal — exclusive live-stream price drop
7. Social proof — reviews, sales numbers, repurchase rate
8. Urgency — limited stock, countdown, strong call to action

Requirements:
- Sound like a real TikTok creator, not a commercial
- Use phrases like "besties", "y'all", "ok but seriously", "look at this", "run don't walk"
- NEVER use AI clichés like "in the ever-evolving world of..."
- Each script must be short enough for a quick cut video
- DO NOT fabricate prices or claims not mentioned in the product info

Product Info:
{product_info}

Return ONLY a JSON array: ["script1", "script2", ...]"""

PROMPT_TEMPLATES = {
    "zh": DEFAULT_PROMPT_TEMPLATE_ZH,
    "en": DEFAULT_PROMPT_TEMPLATE_EN,
}


class ScriptGenerator:
    """Generates broadcast scripts for a product using an LLM."""

    def __init__(
        self,
        llm_client,
        *,
        prompt_template: str | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self._llm = llm_client
        self._custom_template = prompt_template
        self._system_prompt = (
            system_prompt or "你是一个专业的直播带货主播。只输出JSON数组，不要任何解释。"
        )

    async def generate(
        self,
        *,
        url: str = "",
        product_info: str = "",
        num_scripts: int | None = None,
        lang: str = "zh",
    ) -> tuple[str, list[str]]:
        """Generate scripts for a product.

        Args:
            url: Product page URL.
            product_info: Optional product description to include in prompt.
            num_scripts: Target number of scripts (hint only, not enforced).
            lang: Language — "zh" for Chinese, "en" for English.

        Returns:
            (product_name, list of script strings). Name is empty on failure.
        """
        template = self._custom_template or PROMPT_TEMPLATES.get(lang, DEFAULT_PROMPT_TEMPLATE_ZH)
        info_parts = []
        page_title = ""

        # Fetch product page content first
        if url:
            page_title, page_text = await self._fetch_url(url)
            if page_text:
                info_parts.append(f"商品页面内容:\n{page_text[:3000]}")
            else:
                info_parts.append(f"商品链接: {url}")
        if product_info:
            info_parts.append(product_info)
        if not info_parts:
            info_parts.append("（请根据你的知识生成通用直播脚本）")

        prompt = template.format(product_info="\n".join(info_parts))

        if num_scripts:
            prompt += f"\n\n请生成恰好{num_scripts}段脚本。"

        try:
            raw = await self._llm_json(prompt, max_tokens=2048)
            scripts = self._parse_response(raw)
            return (page_title, scripts)
        except Exception as exc:
            logger.error("Script generation failed: %s", exc)
            return ("", [])

    async def _llm_json(self, prompt: str, max_tokens: int) -> str:
        """One-shot LLM call with JSON mode + retry-on-truncation."""
        for attempt in range(3):
            tok = max_tokens * (2 ** attempt)
            try:
                resp = await self._llm._client.chat.completions.create(
                    model=self._llm._model,
                    messages=[
                        {"role": "system", "content": self._system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=tok,
                    temperature=0.7,
                    response_format={"type": "json_object"},
                )
                raw = resp.choices[0].message.content or ""
                finish = resp.choices[0].finish_reason
                logger.info("LLM: %d chars, finish=%s, attempt=%d, max_tokens=%d",
                            len(raw), finish, attempt + 1, tok)
            except Exception as api_err:
                logger.warning("LLM API error (attempt %d/%d): %s", attempt + 1, 3, api_err)
                if attempt < 2:
                    await asyncio.sleep(2)
                    continue
                raise

            if not raw.strip():
                logger.warning("Empty response (attempt %d/%d)", attempt + 1, 3)
                if attempt < 2:
                    await asyncio.sleep(2)
                    continue
                raise RuntimeError("LLM returned empty response after 3 attempts")

            # Strip markdown fences before validation
            clean = raw.strip()
            if clean.startswith("```"):
                lines = clean.split("\n")
                clean = "\n".join(lines[1:]) if len(lines) > 1 else clean
            if clean.endswith("```"):
                clean = clean[:-3].strip()

            # Verify it's parseable JSON; retry if truncated
            try:
                json.loads(clean)
                return clean
            except json.JSONDecodeError:
                if finish == "length" and attempt < 2:
                    logger.warning("JSON truncated (finish=length), retrying with %d tokens...",
                                   tok * 2)
                    await asyncio.sleep(2)
                    continue
                raise RuntimeError(
                    f"LLM returned invalid JSON after {attempt + 1} attempts"
                ) from None

    def _parse_response(self, raw: str) -> list[str]:
        """Parse LLM JSON response into script list. No fallback hacks."""
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:]) if len(lines) > 1 else raw
        if raw.endswith("```"):
            raw = raw[:-3].strip()
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            raw = raw[start:end]
        scripts = json.loads(raw)
        if isinstance(scripts, list):
            return [s.strip() for s in scripts if isinstance(s, str) and s.strip()]
        return []

    async def _fetch_url(self, url: str) -> tuple[str, str | None]:
        """Fetch product page content. Returns (title, page_text)."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                        "Accept-Language": "zh-CN,zh;q=0.9",
                    },
                    follow_redirects=True,
                )
                resp.raise_for_status()
                html = resp.text
        except Exception as exc:
            logger.warning("Failed to fetch URL %s: %s", url, exc)
            return "", None

        # Extract title
        title = ""
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        if title_match:
            title = title_match.group(1).strip()

        # Detect anti-bot pages (TikTok security check, CAPTCHA, etc.)
        bot_titles = {"security check", "captcha", "access denied", "just a moment"}
        if title.lower() in bot_titles or len(html) < 500:
            logger.warning("Anti-bot page detected for %s (title=%s)", url, title)
            return "", None

        # Extract meta description
        desc = ""
        desc_match = re.search(
            r'<meta[^>]+name="description"[^>]+content="([^"]+)"',
            html, re.I,
        )
        if not desc_match:
            desc_match = re.search(
                r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"',
                html, re.I,
            )
        if desc_match:
            desc = desc_match.group(1).strip()

        # Strip all HTML tags to get visible text
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.I | re.S)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        # Truncate to ~2000 chars of visible text
        if len(text) > 2000:
            text = text[:2000]

        parts = []
        if title:
            parts.append(f"标题: {title}")
        if desc:
            parts.append(f"描述: {desc}")
        if text:
            parts.append(f"正文: {text}")

        result = "\n".join(parts)
        logger.info("Fetched %d chars from %s, title=%s", len(result), url, title)
        return title, result if result else None
