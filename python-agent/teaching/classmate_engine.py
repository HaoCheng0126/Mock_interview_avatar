"""Classmate Engine — AI classmate behavior decision & speech generation.

Integrates with teaching.agent to add social classroom dynamics.
AI classmates use DashScope CosyVoice TTS for distinct voices.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time

from teaching.persona_manager import PersonaManager
from teaching.tts_client import TtsClient

logger = logging.getLogger(__name__)


class ClassmateEngine:
    """Manages AI classmates — decides when they speak and what they say."""

    # Per-classmate fallback texts — each classmate sounds distinct
    _FALLBACKS = {
        "小明": {
            "interjection": "我有个问题，为什么不能反过来想呢？",
            "quiz_guess": "我先猜一个，我觉得可能是A！",
            "interaction_answer": "我觉得要看一看理由，不能只跟着大家走。",
        },
        "小红": {
            "interjection": "老师老师，我发现要先看线索，再做决定。",
            "quiz_guess": "让我想想……我选A，因为我记得刚才讲过的。",
            "interaction_answer": "我会先想一想，不急着回答，要想清楚再说。",
        },
        "小刚": {
            "interjection": "我觉得可以先试一种办法，再看结果对不对。",
            "quiz_guess": "肯定是A！反正我直觉超准的，冲就对了！",
            "interaction_answer": "跟着大家一起走应该对吧？不过我再想想……",
        },
        "小美": {
            "interjection": "我有点不确定，是不是要先观察再回答？",
            "quiz_guess": "嗯…我猜是A，虽然不太确定。",
            "interaction_answer": "我观察到一点点东西，不知道对不对……",
        },
    }
    _DEFAULT_FALLBACKS = {
        "interjection": "我有个小问题，这里是不是要先想一想再判断呀？",
        "quiz_guess": "我先猜一个，我觉得可能是A。",
        "interaction_answer": "我觉得要看一看理由，不能只跟着大家走。",
    }

    def __init__(self, persona: PersonaManager, llm_factory,
                 tts_client: TtsClient | None = None) -> None:
        self._persona = persona
        self._classmates = persona.classmates
        self._tts = tts_client
        # One LLM client per classmate (shared system prompt)
        self._llm_clients: dict[str, object] = {}
        for cm in self._classmates:
            name = cm["name"]
            prompt = persona.build_classmate_prompt(name)
            self._llm_clients[name] = llm_factory(name, prompt)
        self._speak_cooldown: dict[str, float] = {}  # name → next_allowed_at
        self._enabled = True
        self._total_speaks = 0
        self._recent_texts: list[str] = []

    def _fallback_text(self, name: str, kind: str) -> str:
        """Get a classmate-specific fallback text for when LLM fails."""
        fb = self._FALLBACKS.get(name, {})
        return fb.get(kind, self._DEFAULT_FALLBACKS.get(kind, "嗯……"))

    @property
    def enabled(self) -> bool:
        return self._enabled and len(self._classmates) > 0

    def disable(self) -> None:
        self._enabled = False

    def enable(self) -> None:
        self._enabled = True

    # -- Decision: should a classmate speak now? -------------------------------

    def should_interject(self, knowledge_index: int = 999) -> str | None:
        """30% chance a classmate speaks after a knowledge point.
        Never interjects on the very first point (index 0).
        Returns classmate name or None.
        """
        if not self.enabled:
            return None
        if knowledge_index == 0:
            return None
        if random.random() > 0.30:
            return None
        available = [
            name for name in self._llm_clients
            if time.time() >= self._speak_cooldown.get(name, 0)
        ]
        if not available:
            return None
        return random.choice(available)

    def should_answer_interaction(self) -> str | None:
        """Choose a classmate to answer teacher's interaction question.
        Used when real student doesn't respond within timeout.
        Returns classmate name or None.
        """
        if not self.enabled:
            return None
        available = list(self._llm_clients.keys())
        if not available:
            return None
        return random.choice(available)

    # -- Speech generation -----------------------------------------------------

    async def _llm_speak(self, llm, user_msg: str, max_tokens: int) -> str:
        """One-shot non-streaming LLM call. Avoids the streaming empty-response bug."""
        for attempt in range(3):
            try:
                resp = await llm._client.chat.completions.create(
                    model=llm._model,
                    messages=llm._messages + [{
                        "role": "user",
                        "content": (
                            f"{user_msg}\n"
                            "只输出一句完整的小朋友口语，必须以。？！～结尾。"
                        ),
                    }],
                    max_tokens=max_tokens,
                    temperature=0.9,
                )
                choice = resp.choices[0]
                finish_reason = getattr(choice, "finish_reason", None)
                raw = choice.message.content or ""
                speech = raw.strip()
                if speech and finish_reason in (None, "stop") and not self._looks_incomplete(speech):
                    return speech
                if speech and finish_reason not in (None, "stop"):
                    logger.warning(
                        "Classmate LLM stopped by %s (attempt %d/3): %s",
                        finish_reason, attempt + 1, speech[:80],
                    )
                    if attempt < 2:
                        await asyncio.sleep(1)
                    continue
                if speech:
                    logger.warning(
                        "Classmate LLM incomplete (attempt %d/3): %s",
                        attempt + 1, speech[:80],
                    )
                    if attempt < 2:
                        await asyncio.sleep(1)
                    continue
                logger.warning("Classmate LLM empty (attempt %d/3)", attempt + 1)
                if attempt < 2:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.warning("Classmate LLM error (attempt %d/3): %s", attempt + 1, e)
                if attempt < 2:
                    await asyncio.sleep(1)
        return ""  # All retries exhausted

    async def generate_interjection(self, name: str, context: str) -> dict | None:
        """Generate a spontaneous interjection from a classmate."""
        llm = self._llm_clients.get(name)
        if not llm:
            return None
        prompt = self._persona.build_classmate_interjection_prompt(name, context)
        try:
            llm.reset_context()
            llm._system_prompt = prompt
            llm._messages = [{"role": "system", "content": prompt}]
            speech = await self._llm_speak(
                llm,
                "请自然地发言，直接说出你想问的内容或想说的话，不要问'我可以问问题吗'这类开场白。"
                "格式必须是 QUESTION: 发言内容 或 STATEMENT: 发言内容。",
                max_tokens=120,
            )
            if speech:
                self._speak_cooldown[name] = time.time() + 15
                self._total_speaks += 1
                intent, text = self._parse_interjection(speech)
                text = self._clean_speech(name, text)
                if self._is_teaser_speech(text) or self._is_recent_duplicate(text):
                    text = self._fallback_text(name, "interjection")
                    intent = "statement"
                logger.info("🗣️  %s", text[:60])
                return self._message(
                    name, "interjection", text,
                    intent=intent,
                    audio_url=await self._synthesize_audio(name, text),
                )
        except Exception as e:
            logger.warning("Classmate %s interjection failed: %s", name, e)
        return await self._fallback_message(name, "interjection",
            self._fallback_text(name, "interjection"))

    async def generate_quiz_answer(self, name: str, question: str) -> dict | None:
        """Generate a classmate's answer to a quiz question."""
        llm = self._llm_clients.get(name)
        if not llm:
            return None
        prompt = self._persona.build_classmate_quiz_answer_prompt(name, question)
        try:
            llm.reset_context()
            llm._system_prompt = prompt
            llm._messages = [{"role": "system", "content": prompt}]
            answer = await self._llm_speak(llm, "请回答这道题", max_tokens=100)
            if answer:
                text = self._clean_speech(name, answer)
                if self._is_recent_duplicate(text):
                    text = self._fallback_text(name, "quiz_guess")
                logger.info("📝 %s answers quiz: %s", name, text[:60])
                return self._message(
                    name, "quiz_guess", text,
                    audio_url=await self._synthesize_audio(name, text),
                )
        except Exception as e:
            logger.warning("Classmate %s quiz answer failed: %s", name, e)
        return await self._fallback_message(name, "quiz_guess",
            self._fallback_text(name, "quiz_guess"))

    async def generate_interaction_answer(self, name: str, question: str) -> dict | None:
        """Generate a classmate's answer to teacher's interaction question."""
        llm = self._llm_clients.get(name)
        if not llm:
            return None
        prompt = self._persona.build_classmate_prompt(name)
        try:
            llm.reset_context()
            llm._system_prompt = prompt
            llm._messages = [{"role": "system", "content": prompt}]
            user_msg = f"老师问：{question}\n请用小朋友的语气回答，1-2句话。"
            answer = await self._llm_speak(llm, user_msg, max_tokens=120)
            if answer:
                text = self._clean_speech(name, answer)
                if self._is_recent_duplicate(text):
                    text = self._fallback_text(name, "interaction_answer")
                logger.info("🙋 %s answers interaction: %s", name, text[:60])
                return self._message(
                    name, "interaction_answer", text,
                    audio_url=await self._synthesize_audio(name, text),
                )
        except Exception as e:
            logger.warning("Classmate %s interaction answer failed: %s", name, e)
        return await self._fallback_message(name, "interaction_answer",
            self._fallback_text(name, "interaction_answer"))

    async def _fallback_message(self, name: str, kind: str, text: str) -> dict | None:
        if name not in self._llm_clients:
            return None
        self._speak_cooldown[name] = time.time() + 15
        self._total_speaks += 1
        return self._message(
            name, kind, text,
            audio_url=await self._synthesize_audio(name, text),
        )

    def _message(
        self,
        name: str,
        kind: str,
        text: str,
        audio_url: str | None = None,
        intent: str | None = None,
    ) -> dict:
        text = self._clean_speech(name, text)
        self._remember_text(text)
        message = {
            "speaker": name,
            "kind": kind,
            "text": text,
            "audio_url": audio_url,
        }
        if intent:
            message["intent"] = intent
        return message

    async def _synthesize_audio(self, name: str, text: str) -> str | None:
        if not self._tts:
            return None
        yaml_voice = self.get_classmate_voice(name)
        voice = self._tts.get_voice_for(name, yaml_voice=yaml_voice)
        start = time.time()
        audio_url = await asyncio.to_thread(self._tts.synthesize, text, voice=voice)
        logger.info(
            "Classmate TTS finished in %.2fs: speaker=%s voice=%s has_audio=%s",
            time.time() - start,
            name,
            voice,
            bool(audio_url),
        )
        if audio_url:
            return audio_url
        if voice != "longanhuan":
            logger.warning(
                "Classmate TTS failed for %s voice=%s; retrying default child voice",
                name, voice,
            )
            return await asyncio.to_thread(
                self._tts.synthesize, text, voice="longanhuan"
            )
        return None

    @staticmethod
    def _clean_speech(name: str, text: str) -> str:
        speech = (text or "").strip()
        return re.sub(rf"^\s*{re.escape(name)}\s*[:：]\s*", "", speech).strip()

    @staticmethod
    def _parse_interjection(text: str) -> tuple[str, str]:
        speech = (text or "").strip()
        match = re.match(r"^(QUESTION|STATEMENT)\s*[:：]\s*(.+)$", speech, re.I | re.S)
        if match:
            label = match.group(1).lower()
            content = match.group(2).strip()
            intent = "question" if label == "question" else "statement"
            return intent, content
        return ("statement", speech)

    @staticmethod
    def _looks_incomplete(text: str) -> bool:
        speech = (text or "").strip()
        if not speech:
            return True
        if speech.count("“") != speech.count("”") or speech.count("\"") % 2 == 1:
            return True
        if re.search(r"[，,：:、]$", speech):
            return True
        return not re.search(r"[。？！?!～~]$", speech)

    @staticmethod
    def _is_teaser_speech(text: str) -> bool:
        speech = (text or "").strip()
        if not speech:
            return False
        return bool(re.search(
            r"(等一下等一下|我有(个|一?个)?[^。？！?!～~]{0,8}(想法|主意)|"
            r"我想到(一个)?小发现|快告诉大家|我们听着|听我说)",
            speech,
        ))

    def _is_recent_duplicate(self, text: str) -> bool:
        return self._clean_for_compare(text) in {
            self._clean_for_compare(t) for t in self._recent_texts[-6:]
        }

    def _remember_text(self, text: str) -> None:
        if not text:
            return
        self._recent_texts.append(text)
        if len(self._recent_texts) > 12:
            self._recent_texts = self._recent_texts[-12:]

    @staticmethod
    def _clean_for_compare(text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    def get_classmate_voice(self, name: str) -> str:
        """Get a classmate's voice description for TTS."""
        for cm in self._classmates:
            if cm.get("name") == name:
                return cm.get("voice", "")
        return ""
