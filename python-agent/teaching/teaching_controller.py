"""Teaching Controller — state machine for lecture, Q&A, and quiz flows."""

from __future__ import annotations
import asyncio
import copy
import datetime
import logging
import re
import time
from enum import Enum
from teaching.course_component import ComponentMessage
from teaching.pacing_engine import PacingEngine, PacingAction

logger = logging.getLogger(__name__)


class TeachingState(str, Enum):
    IDLE = "idle"
    LECTURING = "lecturing"
    WAITING_INTERACT = "waiting_interact"
    PROCESSING_INTER = "processing_interact"
    ANSWERING = "answering"
    TRANSITIONING = "transitioning"
    QUIZZING = "quizzing"
    QUIZ_RESULT = "quiz_result"


LECTURE_SYSTEM_PROMPT = """\
你是一位面向 4-10 岁小朋友的思维课老师，名字叫"小思老师"。
说话要像幼儿园/小学老师一样亲切可爱，用小朋友能听懂的语言。

规则：
- 每句话不超过 15 个字，用短句
- 用小朋友熟悉的事物打比方（玩具、小动物、吃东西、玩游戏）
- 称呼学生为"小朋友"或"你"
- 每次讲解完一个要点，加一句鼓励的话
- 语气温暖、活泼，像在讲故事
- 避免抽象概念，每个概念都要配一个具体的例子
- 每次根据给定的要点，扩展成 3-5 句自然的讲课语言
"""

QA_SYSTEM_PROMPT = """\
你是一位面向 4-10 岁小朋友的思维课老师"小思老师"。
你正在讲解{chapter_title}，一个小朋友举手向你提问。

规则：
- 先感谢小朋友的提问，肯定他/她（"这个问题问得真好！"）
- 用小朋友能听懂的方式回答，控制在 100 字以内
- 句子要短，用生活化的例子
- 回答完后，自然地带小朋友回到课程（"好啦，我们继续来学习..."）
- 如果小朋友的提问不清楚，温柔地请他/她再说一遍
"""

TRANSITION_PROMPT = """\
你正在给小朋友讲课，刚才小朋友提了一个问题，你已经回答完了。
现在需要自然地带小朋友回到课程内容。

刚才讲到的内容是：{context}
请用 1-2 句亲切的话，把小朋友的注意力拉回课程。
"""

INTERACTION_FEEDBACK_PROMPT = """\
你正在给小朋友讲课，你刚才问了小朋友一个问题：{question}
小朋友的回答是：{response}

请用 2-3 句亲切的话给小朋友反馈。先肯定他/她的回答，然后自然地继续讲课。
"""


class TeachingController:
    def __init__(self, *, agent, course_manager, llm_client,
                 persona_manager=None, manager_agent=None,
                 pacing_engine=None,
                 classmate_engine=None, chunk_delay_ms: int = 200,
                 course_end_pause_seconds: float = 0.3) -> None:
        self._agent = agent
        self._cm = course_manager
        self._llm = llm_client
        self._persona = persona_manager  # PersonaManager or None
        self._manager = manager_agent    # ManagerAgent or None (deprecated, use pacing_engine)
        self._pacing = pacing_engine     # PacingEngine (preferred)
        self._classmates = classmate_engine  # ClassmateEngine or None
        self._chunk_delay = chunk_delay_ms / 1000.0
        self._course_end_pause = course_end_pause_seconds

        self._state = TeachingState.IDLE
        self._task: asyncio.Task | None = None
        self._stopped = False

        # TTS idle tracking
        self._tts_idle = asyncio.Event()
        self._tts_idle.set()
        self._idle_count = 0
        self._timeout_count = 0

        # Position tracking
        self._current_chapter_id: str | None = None
        self._current_skeleton_index: int = 0
        self._breakpoint: dict | None = None

        # Quiz
        self._quiz_answer: asyncio.Event | None = None
        self._quiz_chosen: str | None = None
        self._quiz_started_at: str | None = None

        # Raise-hand
        self._hand_raised = asyncio.Event()
        self._hand_cancelled = False

        # First-chapter greeting flag
        self._greeting_sent = False

        # Course end: set when goodbye announcement is sent
        self._course_ended = asyncio.Event()
        self._course_closed = asyncio.Event()

        # Pre-polish cache: {chapter_id: {index: polished_text}}
        self._pre_polished: dict[str, dict[int, str]] = {}

        # Component queue — used by get_status() for tests & WS replay
        self._component_queue: list[dict] = []
        self._component_seq = 0
        self._delivered_audio_seq = 0

        # Message log — used for transcript history & WS replay
        self._message_log: list[dict] = []
        self._message_seq = 0

        self._interaction_timeout_task: asyncio.Task | None = None

    @property
    def state(self) -> TeachingState:
        return self._state

    def start(self) -> None:
        if self._state != TeachingState.IDLE:
            return
        self._stopped = False
        self._pre_polished.clear()
        self._course_ended.clear()
        self._course_closed.clear()
        self._state = TeachingState.LECTURING
        self._task = asyncio.create_task(self._run_lecture_loop())

    def stop(self) -> None:
        self._stopped = True
        self._hand_raised.set()
        if self._quiz_answer:
            self._quiz_answer.set()
        if self._task and not self._task.done():
            self._task.cancel()
        if self._interaction_timeout_task and not self._interaction_timeout_task.done():
            self._interaction_timeout_task.cancel()
        self._state = TeachingState.IDLE
        self._task = None

    def pause(self) -> None:
        pass  # no-op if not lecturing

    def resume(self) -> None:
        pass

    async def skip_chapter(self) -> dict:
        """Skip to the next chapter. Returns info about what was skipped to."""
        if self._state not in (TeachingState.LECTURING, TeachingState.ANSWERING,
                                TeachingState.TRANSITIONING, TeachingState.WAITING_INTERACT,
                                TeachingState.QUIZZING, TeachingState.QUIZ_RESULT):
            return {"success": False, "error": f"Cannot skip in state {self._state.value}"}
        # Cancel current activity
        if self._task and not self._task.done():
            self._task.cancel()
        if self._interaction_timeout_task and not self._interaction_timeout_task.done():
            self._interaction_timeout_task.cancel()
        self._hand_raised.set()
        self._tts_idle.set()
        self._quiz_answer = None
        await self.stop_classmate_audio()
        if self._agent:
            try:
                await self._agent.send_interrupt()
            except Exception:
                pass
        # Find next chapter
        current_id = self._current_chapter_id
        if not current_id:
            # Not started yet — start from first chapter
            next_ch = self._cm.get_first_chapter()
        else:
            # Skip past current chapter's quiz/interaction to next chapter
            next_ch = self._cm.get_next_chapter(current_id)
        if not next_ch:
            return {"success": False, "error": "No next chapter — already at the end"}
        self._state = TeachingState.LECTURING
        self._current_chapter_id = next_ch["id"]
        self._current_skeleton_index = 0
        self._task = asyncio.create_task(self._skip_from(next_ch))
        logger.info("⏭️  Skipped to chapter: %s", next_ch["title"])
        return {"success": True, "chapter": {"id": next_ch["id"], "title": next_ch["title"]}}

    async def _skip_from(self, chapter: dict) -> None:
        """Play from given chapter through all remaining chapters."""
        ch = chapter
        while ch is not None and not self._stopped:
            self._current_chapter_id = ch["id"]
            self._current_skeleton_index = 0
            await self._broadcast_chapter(ch)
            if self._stopped:
                return
            if self._state == TeachingState.ANSWERING:
                return
            if self._state == TeachingState.WAITING_INTERACT:
                return
            if self._state == TeachingState.QUIZZING:
                await self._handle_quiz(ch)
            ch = self._cm.get_next_chapter(ch["id"])
        if not self._stopped:
            await self._end_course()

    def raise_hand(self) -> None:
        if self._state == TeachingState.LECTURING:
            self._breakpoint = {
                "chapter_id": self._current_chapter_id,
                "skeleton_index": self._current_skeleton_index,
            }
            self._state = TeachingState.ANSWERING
            self._hand_raised.set()
            self._tts_idle.set()
            logger.info("🙋 Hand raised — breakpoint: %s", self._breakpoint)
        elif self._state in (TeachingState.ANSWERING, TeachingState.TRANSITIONING):
            # Re-raise: cancel current response, stay in ANSWERING for new question
            if self._task and not self._task.done():
                self._task.cancel()
            self._hand_raised.set()
            self._tts_idle.set()
            logger.info("🙋 Hand re-raised during %s — cancelling current response", self._state.value)

    def cancel_hand(self) -> None:
        if self._state == TeachingState.ANSWERING:
            self._hand_cancelled = True
            self._hand_raised.set()
            self._state = TeachingState.LECTURING
            # Don't restart lecture here — the QA flow (if still running) will
            # call _resume_lecture via _generate_transition when it finishes.
            # If no QA was started, resume from breakpoint.
            logger.info("🙋 Hand cancelled — will resume naturally")

    def cancel_interaction_timeout(self) -> None:
        if self._interaction_timeout_task and not self._interaction_timeout_task.done():
            self._interaction_timeout_task.cancel()
        self._interaction_timeout_task = None

    def answer_quiz(self, chapter_id: str, answer: str) -> None:
        if self._state != TeachingState.QUIZZING:
            raise ValueError("Not in QUIZZING state")
        if chapter_id != self._current_chapter_id:
            raise ValueError(f"Quiz answer for '{chapter_id}' but current quiz is '{self._current_chapter_id}'")
        self._quiz_chosen = answer
        if self._quiz_answer:
            self._quiz_answer.set()

    def notify_platform_idle(self) -> None:
        self._idle_count += 1
        self._tts_idle.set()

    async def await_tts_idle(self, timeout: float = 30.0) -> None:
        """Wait for platform TTS idle, with timeout. For use by listener."""
        try:
            await asyncio.wait_for(self._tts_idle.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self._timeout_count += 1
        self._tts_idle.clear()

    async def stop_classmate_audio(self) -> None:
        """Tell the frontend to stop any out-of-band classmate audio."""
        await self._send_component("play_audio", "stop", {"speaker": "classmate"})

    def get_status(self) -> dict:
        chapters_count = self._cm.get_chapter_count()
        current_idx = 0
        chapter_title = ""
        chapter = None
        if self._current_chapter_id:
            try:
                for i in range(chapters_count):
                    ch = self._cm.get_chapter_by_index(i)
                    if ch and ch["id"] == self._current_chapter_id:
                        current_idx = i
                        chapter_title = ch.get("title", "")
                        chapter = ch
                        break
            except (ValueError, IndexError):
                pass
        # Include latest components and quiz info
        quiz_info = None
        if chapter and chapter.get("quiz") and self._state == TeachingState.QUIZZING:
            quiz_info = {
                "question": chapter["quiz"]["question"],
                "options": chapter["quiz"]["options"],
                "chapter_id": chapter["id"],
                "started_at": self._quiz_started_at,
                "timeout_s": 90,
            }
        interaction_info = None
        if chapter and chapter.get("interaction") and self._state == TeachingState.WAITING_INTERACT:
            interaction_info = {
                "text": chapter["interaction"]["prompt"],
                "chapter_id": chapter["id"],
            }
        visual_info = None
        if chapter and chapter.get("visual"):
            ref = chapter["visual"]["ref"]
            for c in self._cm.get_cards():
                if c["id"] == ref:
                    visual_info = {
                        "type": chapter["visual"]["type"],
                        "id": c.get("id", ""),
                        "title": c.get("title", ""),
                        "content": c.get("content", ""),
                        "image": c.get("image"),
                    }
                    break
        # Components and messages are pushed via WebSocket but also kept
        # in queues for status endpoint (debugging / initial sync)
        recent_components = [
            copy.deepcopy(entry)
            for entry in self._component_queue[-20:]
        ]
        # Find latest whiteboard + play_audio for status
        whiteboard_info = None
        play_audio_info = None
        for entry in reversed(self._component_queue):
            if entry["type"] in ("whiteboard_step", "whiteboard_compare") and not whiteboard_info:
                whiteboard_info = {"type": entry["type"], "data": entry["data"]}
            if entry["type"] == "play_audio" and not play_audio_info:
                if entry["seq"] > self._delivered_audio_seq:
                    play_audio_info = {"type": entry["type"], "action": entry["action"], "data": entry["data"]}
                    self._delivered_audio_seq = entry["seq"]
            if whiteboard_info and play_audio_info:
                break
        if play_audio_info:
            whiteboard_info = play_audio_info
        return {
            "state": self._state.value,
            "currentChapter": {
                "id": self._current_chapter_id,
                "title": chapter_title,
                "skeleton": [
                    self._step_text(step)
                    for step in chapter.get("skeleton", [])
                ] if chapter else [],
            } if self._current_chapter_id else None,
            "currentChapterIndex": current_idx,
            "currentSkeletonIndex": self._current_skeleton_index,
            "totalChapters": chapters_count,
            "componentSeq": self._component_seq,
            "components": recent_components,
            "messageSeq": self._message_seq,
            "messages": self._message_log[-20:],
            "courseEnded": self._course_closed.is_set(),
            "quiz": quiz_info,
            "interaction": interaction_info,
            "visual": visual_info,
            "whiteboard": whiteboard_info,
        }

    # -- lecture loop --

    async def _run_lecture_loop(self) -> None:
        chapter = self._cm.get_first_chapter()
        while chapter is not None and not self._stopped:
            self._current_chapter_id = chapter["id"]
            self._current_skeleton_index = 0
            await self._broadcast_chapter(chapter)
            if self._stopped:
                return
            # Do NOT advance to next chapter if hand was raised
            if self._state == TeachingState.ANSWERING:
                return
            if self._state == TeachingState.WAITING_INTERACT:
                return
            if self._state == TeachingState.QUIZZING:
                await self._handle_quiz(chapter)
            chapter = self._cm.get_next_chapter(chapter["id"])
        if not self._stopped:
            await self._end_course()

    async def _end_course(self) -> None:
        """Send goodbye announcement and signal course completion.
        Only fires if lecture truly ended (not interrupted by QA).
        Keeps state=LECTURING during goodbye so student can still raise hand."""
        if self._state == TeachingState.ANSWERING:
            return  # Student is still in QA — don't end yet
        await self.await_tts_idle(timeout=20.0)
        await asyncio.sleep(self._course_end_pause)
        # Summary + goodbye. LLM generates summary, we suffix the goodbye.
        summary = "今天我们学到了很多新知识，你表现得真棒！"
        try:
            chapters_covered = []
            ch = self._cm.get_first_chapter()
            while ch is not None:
                chapters_covered.append(ch.get("title", ""))
                if ch["id"] == self._current_chapter_id:
                    break
                ch = self._cm.get_next_chapter(ch["id"])
            titles = "、".join(chapters_covered[:5])
            if titles:
                prompt = f"你刚给小朋友讲完了这些内容：{titles}。请用1-2句亲切活泼的话总结今天学到了什么，像幼儿园老师。"
                try:
                    resp = await self._llm._client.chat.completions.create(
                        model=self._llm._model,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=256,
                        temperature=0.7,
                    )
                    s = (resp.choices[0].message.content or "").strip()
                    if s:
                        summary = s
                except Exception:
                    pass
        except Exception:
            pass
        goodbye = f"{summary} 今天的课程就到这里啦，小朋友我们下次再见哦～"
        self.log_message("agent", goodbye)
        await self._agent.send_prompt(goodbye)
        # Stay in LECTURING — student can raise hand during/after goodbye
        self._state = TeachingState.LECTURING
        self._course_ended.set()
        logger.info("📚 Course ended — goodbye sent, waiting for student")

    def mark_course_closed(self) -> None:
        self._course_closed.set()

    async def _resume_lecture(self) -> None:
        bp = self._breakpoint
        if not bp:
            return
        chapter = self._cm.get_chapter(bp["chapter_id"])
        self._current_chapter_id = chapter["id"]
        self._current_skeleton_index = bp["skeleton_index"]
        await self._broadcast_chapter(chapter, start_from=bp["skeleton_index"])
        if self._state == TeachingState.ANSWERING:
            return
        if self._state == TeachingState.WAITING_INTERACT:
            return
        chapter = self._cm.get_next_chapter(chapter["id"])
        while chapter is not None and not self._stopped:
            self._current_chapter_id = chapter["id"]
            self._current_skeleton_index = 0
            await self._broadcast_chapter(chapter)
            if self._stopped:
                return
            if self._state == TeachingState.ANSWERING:
                return
            if self._state == TeachingState.WAITING_INTERACT:
                return
            if self._state == TeachingState.QUIZZING:
                await self._handle_quiz(chapter)
            chapter = self._cm.get_next_chapter(chapter["id"])
        if not self._stopped:
            await self._end_course()

    async def _continue_after_interaction(self, chapter_id: str | None = None) -> None:
        """Continue from the chapter after a teacher-initiated interaction."""
        current_id = chapter_id or self._current_chapter_id
        if not current_id:
            return
        chapter = self._cm.get_next_chapter(current_id)
        self._state = TeachingState.LECTURING
        while chapter is not None and not self._stopped:
            self._current_chapter_id = chapter["id"]
            self._current_skeleton_index = 0
            await self._broadcast_chapter(chapter)
            if self._stopped:
                return
            if self._state == TeachingState.ANSWERING:
                return
            if self._state == TeachingState.WAITING_INTERACT:
                return
            if self._state == TeachingState.QUIZZING:
                await self._handle_quiz(chapter)
            chapter = self._cm.get_next_chapter(chapter["id"])
        if not self._stopped:
            await self._end_course()

    async def _finish_interaction_and_continue(self, chapter_id: str) -> None:
        self.cancel_interaction_timeout()
        self._state = TeachingState.LECTURING
        self._task = asyncio.create_task(self._continue_after_interaction(chapter_id))

    async def _interaction_timeout(self, chapter_id: str, question: str) -> None:
        try:
            await asyncio.sleep(12)
            if self._state != TeachingState.WAITING_INTERACT or self._current_chapter_id != chapter_id:
                return
            if self._classmates and self._classmates.enabled:
                name = self._classmates.should_answer_interaction()
                if name:
                    answer = await self._classmates.generate_interaction_answer(name, question)
                    if answer:
                        await self._emit_classmate_turn(
                            answer,
                            ack_template="{name}说得很好！我们继续往下看～",
                        )
            else:
                msg = "我们先继续往下看，等你想到答案可以再举手告诉老师。"
                self.log_message("agent", msg)
                await self._agent.send_prompt(msg)
            await self._finish_interaction_and_continue(chapter_id)
        except asyncio.CancelledError:
            pass

    async def _broadcast_chapter(self, chapter: dict, start_from: int = 0) -> None:
        self._current_chapter_id = chapter["id"]
        chapter_index = 0
        chapters_count = self._cm.get_chapter_count()
        for idx in range(chapters_count):
            ch = self._cm.get_chapter_by_index(idx)
            if ch and ch.get("id") == chapter["id"]:
                chapter_index = idx
                break
        # chapter_indicator
        await self._send_component("chapter_indicator", "show", {
            "title": chapter["title"],
            "chapter_id": chapter["id"],
            "chapter_index": chapter_index,
            "total_chapters": chapters_count,
        })
        # visual
        visual = chapter.get("visual")
        if visual:
            ref = visual["ref"]
            for c in self._cm.get_cards():
                if c["id"] == ref:
                    await self._send_component(visual["type"], "show", {
                        "id": c.get("id", ""), "title": c.get("title", ""),
                        "content": c.get("content", ""), "image": c.get("image"),
                    })
                    break
        self._chapter_start = time.time()
        skeleton = chapter["skeleton"]
        total = len(skeleton)

        # Send immediate greeting once, before any LLM calls
        if not self._greeting_sent:
            self._greeting_sent = True
            greeting = "小朋友们好！小思老师来啦～我们开始上课吧！"
            self.log_message("agent", greeting)
            await self._agent.send_prompt(greeting)

        # If resuming from breakpoint, send filler + background-polish first point
        polish_task = None
        if start_from > 0:
            self.log_message("agent", "好的，我们继续～")
            await self._agent.send_prompt("好的，我们继续～")
            # Start polishing the first skeleton point in background
            first_point = self._step_text(skeleton[start_from]) if start_from < len(skeleton) else None
            if first_point:
                polish_task = asyncio.create_task(self._polish_skeleton(first_point))

        for i in range(start_from, total):
            if self._stopped:
                return
            step = skeleton[i]
            point = self._step_text(step)
            self._current_skeleton_index = i
            if self._hand_raised.is_set():
                self._hand_raised.clear()
                if self._state == TeachingState.ANSWERING:
                    self._breakpoint = {"chapter_id": chapter["id"], "skeleton_index": i}
                    return
                elif self._hand_cancelled:
                    self._hand_cancelled = False
            await self._send_component("lecture_progress", "update", {
                "segment_current": i + 1, "segment_total": total,
            })
            # Use background polish result for first point, cache for rest
            if i == start_from and polish_task and not polish_task.done():
                polished = await polish_task
            elif i == start_from and polish_task:
                polished = await polish_task  # already done, returns immediately
            else:
                cache = self._pre_polished.get(chapter["id"], {})
                if i in cache:
                    polished = cache.pop(i)
                else:
                    polished = await self._polish_skeleton(point)

            # Check if hand was raised during LLM polish
            if self._state == TeachingState.ANSWERING:
                return

            # Wait for previous prompt TTS to finish (IDLE may arrive during polish)
            try:
                await asyncio.wait_for(self._tts_idle.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                self._timeout_count += 1
            self._tts_idle.clear()
            # Natural pause between knowledge points (classroom pacing)
            await asyncio.sleep(1.5)

            # Check if hand was raised (unblocks us from TTS wait)
            if self._state == TeachingState.ANSWERING:
                return

            # Push whiteboard step for current knowledge point
            await self._send_component("whiteboard_step", "show", {
                "chapter_id": chapter["id"],
                "step_num": i + 1,
                "step_total": total,
                "text": point,
            })
            experience = self._step_experience(step)
            if experience:
                await self._send_component("interactive_scene", "show", {
                    "chapter_id": chapter["id"],
                    "step_num": i + 1,
                    "step_total": total,
                    "primitive": experience.get("primitive", ""),
                    "goal": experience.get("goal", ""),
                    "title": experience.get("title", chapter["title"]),
                    "prompt": experience.get("prompt", ""),
                    "props": experience.get("props", {}),
                })

            self.log_message("agent", polished)
            # Clear idle BEFORE sending prompt so the next wait blocks for THIS TTS
            self._tts_idle.clear()
            await self._agent.send_prompt(polished)

            # --- Pacing Engine: unified classroom rhythm decisions ---
            pacing = self._pacing or self._manager
            if pacing:
                # Reset chapter tracking when starting a new chapter
                if isinstance(pacing, PacingEngine) and i == start_from:
                    pacing.reset_chapter()

                pacing.update_state(
                    chapter_id=chapter["id"],
                    knowledge_index=i,
                    knowledge_total=total,
                    elapsed_seconds=(time.time() - getattr(self, '_chapter_start', time.time())),
                )
                actions = await pacing.evaluate()
                for action in actions:
                    if action.kind == "RE_EXPLAIN":
                        alt = await self._polish_skeleton(f"换个方式再讲一遍：{point}")
                        self.log_message("agent", alt)
                        await self._agent.send_prompt(alt)
                    elif action.kind == "CLASSMATE_SPEAK":
                        await self._execute_pacing_classmate(chapter, point, action)
                    elif action.kind == "SKIP":
                        break  # Skip remaining knowledge points in this chapter

            # --- Background pre-polish next chapter's first point ---
            if i == total - 1:  # Last skeleton point of current chapter
                next_ch = self._cm.get_next_chapter(chapter["id"])
                if next_ch and next_ch.get("skeleton"):
                    next_id = next_ch["id"]
                    first_point = self._step_text(next_ch["skeleton"][0])
                    async def _pre_polish():
                        try:
                            polished = await self._polish_skeleton(first_point)
                            self._pre_polished.setdefault(next_id, {})[0] = polished
                            logger.debug("Pre-polished %s[0]", next_id)
                        except Exception:
                            pass
                    asyncio.create_task(_pre_polish())

        # Post-chapter state
        if chapter.get("quiz"):
            self._state = TeachingState.QUIZZING
        elif chapter.get("interaction"):
            self._state = TeachingState.WAITING_INTERACT
            interaction_text = chapter["interaction"]["prompt"]
            spoken_prompt = self._interaction_spoken_prompt(interaction_text)
            self.log_message("agent", spoken_prompt)
            self._tts_idle.clear()
            await self._agent.send_prompt(spoken_prompt)
            await self.await_tts_idle(timeout=15.0)
            if self._state != TeachingState.WAITING_INTERACT:
                return
            await self._send_component("interaction_prompt", "show", {
                "text": interaction_text, "chapter_id": chapter["id"],
            })
            self.cancel_interaction_timeout()
            self._interaction_timeout_task = asyncio.create_task(
                self._interaction_timeout(chapter["id"], interaction_text)
            )

    @staticmethod
    def _interaction_spoken_prompt(question: str) -> str:
        return f"小侦探，现在轮到你啦！{question}想好后，点蓝色麦克风告诉老师。"

    async def _polish_skeleton(self, point: str) -> str:
        try:
            prompt = LECTURE_SYSTEM_PROMPT
            if self._persona:
                prompt = self._persona.build_teacher_lecture_prompt()
            self._llm.reset_context()
            self._llm._system_prompt = prompt
            self._llm._messages = [{"role": "system", "content": prompt}]
            result = await self._llm.generate(f"请把下面这个讲课要点扩展成生动有趣的口语讲解：\n\n{point}", max_tokens=768)
            if result and result.strip():
                text = result.strip()
                # Strip stage directions: （...）, (...), 【...】
                text = re.sub(r'[（(][^）)]*[）)]', '', text)
                text = re.sub(r'【[^】]*】', '', text)
                if text.strip():
                    return text.strip()
        except Exception:
            pass
        return point

    async def _execute_pacing_classmate(
        self, chapter: dict, point: str, action: PacingAction
    ) -> None:
        """Execute a CLASSMATE_SPEAK pacing action.

        Waits for teacher TTS to finish, then generates and emits a classmate
        interjection. Uses the pre-selected classmate name from the action if
        available, otherwise falls back to should_interject().
        """
        if not self._classmates or not self._classmates.enabled:
            return

        name = action.classmate_name or self._classmates.should_answer_interaction()
        if not name:
            return

        context = f"老师刚讲了「{chapter['title']}」里的一个知识点：{point}"
        started = time.time()
        result_task = asyncio.create_task(
            self._classmates.generate_interjection(name, context)
        )

        # Wait for teacher's current TTS to finish
        try:
            await asyncio.wait_for(self._tts_idle.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("Timed out waiting for teacher TTS before classmate turn")
        self._tts_idle.clear()

        if self._state == TeachingState.ANSWERING:
            result_task.cancel()
            return

        result = await result_task
        logger.info(
            "Classmate turn ready after %.2fs: %s",
            time.time() - started,
            name,
        )
        if result:
            await self._emit_classmate_turn(
                result,
                ack_template="{name}问得真好！我们继续往下看～",
            )

    async def _emit_classmate_turn(
        self,
        result: dict,
        *,
        ack_template: str | None = None,
    ) -> None:
        if self._state == TeachingState.ANSWERING:
            return
        name = result.get("speaker") or "小明"
        text = result.get("text") or ""
        intent = (result.get("intent") or "").lower()
        await self._send_component("classmate_message", "show", result)
        self.log_message("classmate", text, name=name)
        if self._state == TeachingState.ANSWERING:
            return
        ack = None
        if result.get("audio_url"):
            await self._send_component("play_audio", "show", {
                "url": result["audio_url"],
                "speaker": name,
            })
            audio_dur = max(2.0, len(text) / 4.5 + 0.5)  # 4.5 chars/s child voice + frontend buffer
            # Start BOTH intent correction + ack generation during audio playback
            async def _gen_ack():
                nonlocal intent
                corrected = await self._correct_classmate_intent(name, text, intent)
                intent = corrected
                if corrected == "question":
                    return await self._answer_classmate_question(name, text)
                elif corrected == "statement" and "问得" in ack_template:
                    return await self._acknowledge_classmate_statement(name, text)
                elif corrected == "statement":
                    return ack_template.format(name=name)
                elif "问得" in ack_template:
                    return await self._acknowledge_classmate_statement(name, text)
                else:
                    return ack_template.format(name=name)
            ack_task = asyncio.create_task(_gen_ack()) if ack_template else None
            logger.debug("Waiting %.1fs for classmate audio (%d chars)", audio_dur, len(text))
            await asyncio.sleep(audio_dur)
            if ack_task:
                ack = await ack_task
        if ack_template and not ack:
            # No audio_url case — generate ack synchronously
            intent = await self._correct_classmate_intent(name, text, intent)
            if intent == "question":
                ack = await self._answer_classmate_question(name, text)
            elif intent == "statement" and "问得" in ack_template:
                ack = await self._acknowledge_classmate_statement(name, text)
            elif intent == "statement":
                ack = ack_template.format(name=name)
            elif "问得" in ack_template:
                ack = await self._acknowledge_classmate_statement(name, text)
            else:
                ack = ack_template.format(name=name)
        if ack:
            self.log_message("agent", ack)
            await self._agent.send_prompt(ack)
            try:
                await asyncio.wait_for(self._tts_idle.wait(), timeout=15.0)
            except asyncio.TimeoutError:
                pass
            self._tts_idle.clear()
        self._tts_idle.set()

    async def _correct_classmate_intent(self, name: str, text: str, proposed_intent: str) -> str:
        """Use the teacher LLM to verify the classmate's proposed intent."""
        proposed = proposed_intent if proposed_intent in ("question", "statement") else ""
        if not text or not text.strip():
            return proposed or "statement"
        try:
            resp = await self._llm._client.chat.completions.create(
                model=self._llm._model,
                messages=[
                    {"role": "system", "content": (
                        "你是课堂意图校正器。根据小朋友发言判断真实意图，只输出 QUESTION 或 STATEMENT。不要解释。\n"
                        "QUESTION 包括：直接提问、请求老师允许提问、表达不懂或想知道原因。\n"
                        "STATEMENT 包括：回答老师问题、分享观察、分享生活经历、表达赞同。"
                    )},
                    {"role": "user", "content": f"说话人：{name}\n模型给出的意图：{proposed or '未知'}\n发言：{text.strip()}"},
                ],
                max_tokens=12,
                temperature=0.0,
            )
            raw = (resp.choices[0].message.content or "").strip().upper()
            if raw == "QUESTION":
                return "question"
            if raw == "STATEMENT":
                return "statement"
        except Exception:
            pass
        return proposed or "statement"

    async def _acknowledge_classmate_statement(self, name: str, text: str) -> str:
        """Generate a contextual teacher acknowledgment for a classmate's statement.

        Uses the teacher LLM to craft a response that actually addresses what the
        student said, rather than a fixed template that ignores the content.
        """
        try:
            prompt = (
                "你正在给小朋友上课，一个小朋友举手发言说了一句话。\n"
                "请用1-2句亲切的话回应这个小朋友。\n"
                "先肯定他/她的发言（要具体回应他/她说的内容），然后自然地继续讲课。\n"
                "语气要热情活泼，像幼儿园老师一样。\n"
                f"小朋友的名字：{name}\n"
                f"小朋友的发言：{text.strip()}"
            )
            resp = await self._llm._client.chat.completions.create(
                model=self._llm._model,
                messages=[
                    {"role": "system", "content": LECTURE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=200,
                temperature=0.7,
            )
            ack = (resp.choices[0].message.content or "").strip()
            if ack:
                return ack
        except Exception:
            pass
        # Fallback: still more varied than the old hardcoded line
        return f"{name}说得真好！我们继续往下看～"

    async def _answer_classmate_question(self, name: str, question: str) -> str:
        """Non-streaming LLM call to answer a classmate's question."""
        try:
            prompt = QA_SYSTEM_PROMPT.format(
                chapter_title=self._current_chapter_id or "这节课"
            )
            resp = await self._llm._client.chat.completions.create(
                model=self._llm._model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"{name}刚才问：{question}\n请用1-2句小朋友能听懂的话回答这个问题，再自然回到课程。"},
                ],
                max_tokens=180,
                temperature=0.7,
            )
            choice = resp.choices[0]
            finish_reason = getattr(choice, "finish_reason", None)
            answer = (choice.message.content or "").strip()
            if finish_reason not in (None, "stop"):
                logger.warning(
                    "Rejected truncated classmate-question answer: finish_reason=%s text=%s",
                    finish_reason,
                    answer[:80],
                )
                return f"{name}问了一个很好的问题！我们记住它，学完这节课再来回答，好不好？"
            if answer and answer.strip():
                return answer
        except Exception:
            pass
        return f"{name}问了一个很好的问题！我们记住它，学完这节课再来回答，好不好？"

    @staticmethod
    def _step_text(step) -> str:
        if isinstance(step, dict):
            return str(step.get("text", "")).strip()
        return str(step).strip()

    @staticmethod
    def _step_experience(step) -> dict | None:
        if not isinstance(step, dict):
            return None
        experience = step.get("experience")
        return experience if isinstance(experience, dict) else None

    async def _send_component(self, ctype: str, action: str, data: dict) -> None:
        msg = ComponentMessage(type=ctype, action=action, data=data, timestamp=int(time.time() * 1000))
        self._component_seq += 1
        entry = {"seq": self._component_seq, "type": ctype, "action": action, "data": data}
        self._component_queue.append(entry)
        if len(self._component_queue) > 100:
            self._component_queue = self._component_queue[-50:]
        # Push to frontend via WebSocket (primary path)
        from teaching.session import ws_broadcast
        await ws_broadcast({"type": "component", "component": ctype, "action": action, "data": data})
        # Also try sending via platform (for scene.switchVideo etc.)
        await self._agent.send_custom_event(None, ctype, {"action": action, "data": data, "timestamp": msg.timestamp})

    def log_message(self, role: str, text: str, name: str | None = None) -> None:
        """Record message locally and push to frontend via WebSocket."""
        import asyncio as _asyncio
        self._message_seq += 1
        entry = {"seq": self._message_seq, "role": role, "text": text}
        if name:
            entry["name"] = name
        self._message_log.append(entry)
        if len(self._message_log) > 200:
            self._message_log = self._message_log[-100:]
        # Push via WS (fire-and-forget)
        from teaching.session import ws_broadcast
        ws_msg = {"type": "message", "role": role, "text": text}
        if name:
            ws_msg["name"] = name
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                _asyncio.create_task(ws_broadcast(ws_msg))
        except RuntimeError:
            pass

    async def _handle_quiz(self, chapter: dict) -> None:
        quiz = chapter["quiz"]

        # ── Create answer event BEFORE any async work ──
        # Must be ready before the quiz component reaches the frontend,
        # otherwise a fast student click races past the event creation.
        self._quiz_answer = asyncio.Event()
        self._quiz_chosen = None

        # Announce quiz start
        announce = "让我们来做个小测验吧！看看你记住了多少～"
        self.log_message("agent", announce)
        await self._agent.send_prompt(announce)
        # Wait for announcement TTS to finish before showing quiz card
        try:
            await asyncio.wait_for(self._tts_idle.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            pass
        self._tts_idle.clear()

        self._quiz_started_at = datetime.datetime.now().isoformat()
        await self._send_component("quiz", "show", {
            "question": quiz["question"], "options": quiz["options"], "chapter_id": chapter["id"],
            "started_at": self._quiz_started_at, "timeout_s": 90,
        })
        if self._classmates and self._classmates.enabled:
            name = self._classmates.should_answer_interaction()
            if name:
                guess = await self._classmates.generate_quiz_answer(name, quiz["question"])
                if guess:
                    await self._send_component("classmate_message", "show", guess)
                    if guess.get("audio_url"):
                        await self._send_component("play_audio", "show", {
                            "url": guess["audio_url"],
                            "speaker": name,
                        })
        await self._send_component("raise_hand", "update", {"enabled": False})
        try:
            await asyncio.wait_for(self._quiz_answer.wait(), timeout=90.0)
        except asyncio.TimeoutError:
            await self.stop_classmate_audio()
            correct = next(o["text"] for o in quiz["options"] if o["correct"])
            timeout_msg = f"时间到啦！没关系，老师告诉你答案哦～正确答案是：{correct}"
            self.log_message("agent", timeout_msg)
            await self._agent.send_prompt(timeout_msg)
        correct_option = next(o for o in quiz["options"] if o["correct"])
        is_correct = self._quiz_chosen == correct_option["key"]
        self._state = TeachingState.QUIZ_RESULT
        await self.stop_classmate_audio()
        if is_correct:
            await self._send_component("encouragement", "show", {"text": "太棒了！🌟", "style": "star"})
            await self._agent.send_prompt(quiz["explanation_correct"])
        else:
            await self._send_component("encouragement", "show", {"text": "加油！💪", "style": "clap"})
            await self._agent.send_prompt(quiz["explanation_wrong"])
        await self._send_component("quiz_result", "show", {
            "correct": is_correct,
            "explanation": quiz["explanation_correct"] if is_correct else quiz["explanation_wrong"],
            "correct_answer": correct_option["text"] if not is_correct else None,
        })
        await asyncio.sleep(4)
        await self._send_component("raise_hand", "update", {"enabled": True})
        self._state = TeachingState.LECTURING
        self._quiz_answer = None
