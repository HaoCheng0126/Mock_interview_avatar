"""LLM-driven talk show batch generation."""

from __future__ import annotations

import asyncio
import json

from talkshow.show_manager import Bridge, Persona, Segment, Show, ShowBatch, Topic


class TalkshowScriptGenerator:
    """Generates structured talk show batches using an OpenAI-compatible client."""

    def __init__(self, llm_client) -> None:
        self._llm = llm_client

    async def generate_batch(
        self,
        *,
        persona: Persona,
        show: Show,
        topics: list[Topic],
        recent_segments: list[Segment],
        batch_size: int,
        lang: str,
    ) -> ShowBatch:
        prompt = self._build_prompt(
            persona=persona,
            show=show,
            topics=topics,
            recent_segments=recent_segments,
            batch_size=batch_size,
            lang=lang,
        )
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                response = await self._llm._client.chat.completions.create(
                    model=self._llm._model,
                    messages=[
                        {
                            "role": "system",
                            "content": "你是一个专业脱口秀编剧。只输出JSON对象，不要解释。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=4096,
                    temperature=0.8,
                    response_format={"type": "json_object"},
                )
                raw = response.choices[0].message.content or ""
                return self.parse_batch(raw)
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(1)
                    continue
        assert last_error is not None
        raise last_error

    @staticmethod
    def parse_batch(raw: str) -> ShowBatch:
        clean = raw.strip()
        if clean.startswith("```"):
            lines = clean.splitlines()
            clean = "\n".join(lines[1:])
        if clean.endswith("```"):
            clean = clean[:-3].strip()

        data = json.loads(clean)
        segments = [
            Segment(
                topic_id=str(item.get("topic_id") or ""),
                title=str(item.get("title") or ""),
                text=str(item.get("text") or "").strip(),
                beats=[str(beat) for beat in item.get("beats") or []],
            )
            for item in data.get("segments") or []
            if str(item.get("text") or "").strip()
        ]
        if not segments:
            raise ValueError("Generated batch must include non-empty segments")

        bridges = [
            Bridge(
                from_title=str(item.get("from_title") or ""),
                to_title=str(item.get("to_title") or ""),
                text=str(item.get("text") or "").strip(),
            )
            for item in data.get("bridges") or []
            if str(item.get("text") or "").strip()
        ]
        return ShowBatch(
            batch_title=str(data.get("batch_title") or "untitled"),
            segments=segments,
            bridges=bridges,
        )

    @staticmethod
    def build_fallback_batch(segments: list[Segment]) -> ShowBatch:
        usable = [segment for segment in segments if segment.text.strip()]
        if not usable:
            raise ValueError("fallback_segments is empty")
        return ShowBatch(batch_title="fallback", segments=usable, bridges=[])

    def _build_prompt(
        self,
        *,
        persona: Persona,
        show: Show,
        topics: list[Topic],
        recent_segments: list[Segment],
        batch_size: int,
        lang: str,
    ) -> str:
        topic_lines = "\n".join(
            f"- {topic.id}: {topic.title}。{topic.description}" for topic in topics
        )
        boundary_lines = "\n".join(f"- {item}" for item in persona.boundaries)
        recent_lines = "\n".join(
            f"- {segment.title}: {' / '.join(segment.beats)}"
            for segment in recent_segments[-8:]
        )
        return f"""语言: {lang}
节目: {show.title}
演员: {persona.name}
风格: {persona.style}
禁区:
{boundary_lines or "- 无"}

主题池:
{topic_lines or "- workplace: 职场日常"}

最近讲过，避免重复:
{recent_lines or "- 无"}

生成恰好 {batch_size} 个脱口秀 segment，并生成相邻 segment 之间的 bridge。
每个 segment 是完整可表演小段子，约180-350个中文字符，包含铺垫、递进、至少一个清晰笑点和收束。
segment.text 必须是给 TTS 表演的口播稿，不是文章；用自然换行切成3-5个表演小拍。
每行只表达一个节奏动作：铺垫、递进、反问、包袱或收束。
包袱句单独成行；在包袱前一行安排停顿点，在包袱句里安排重音点。
可以使用短句、逗号、省略号、反问句来控制呼吸和轻重，但不要写舞台指令或括号说明。
每个 bridge 40-120个中文字符，必须像演员现场换话题，而不是报幕。
每个 bridge 必须包含两部分：callback 回扣上一段最后的情绪或画面；pivot 自然打开下一段。
bridge 可以有轻微笑点，但不要抢下一个 segment 的主包袱。
禁止写“接下来我们讲下一个话题”这类模板句。

只返回JSON对象，字段和这个示例一致:
{{"batch_title":"职场玄学观察","segments":[{{"topic_id":"workplace","title":"会议室里的时间黑洞","beats":["迟迟不开始的会议观察","黑话重复同一个观点","会议纪要像破案报告"],"text":"会议室有一种特殊的物理规则，只要门一关，时间就开始打折。\\n最开始大家都说，今天高效一点，半小时结束。\\n结果第一个人打开 PPT，说我简单过一下，大家就知道完了。\\n简单这个词在职场里，基本等于先坐稳。"}}],"bridges":[{{"from_title":"会议室里的时间黑洞","to_title":"地铁里的社交礼仪","text":"刚才说会议偷时间，其实办公室还算客气，至少它偷之前还发个日程邀请。地铁就不一样了，它不通知你，直接把你整个人压缩成通勤格式。"}}]}}
"""
