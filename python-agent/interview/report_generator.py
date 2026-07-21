from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime

from interview.models import (
    DimensionAssessment,
    Exchange,
    InterviewReport,
    ReportCover,
    ReportDimensionCommentary,
    ReportHighlightBlock,
    ReportLearningPhase,
    ReportLearningPlan,
    ReportQaAnalysis,
    TranscriptTurn,
)
from interview.prompts import (
    DEFAULT_REPORT_OVERVIEW_PROMPT,
    DEFAULT_REPORT_QA_PROMPT,
    render_template,
)

logger = logging.getLogger(__name__)
REPORT_OVERVIEW_MAX_TOKENS = max(
    1200,
    min(4000, int(os.getenv("INTERVIEW_REPORT_OVERVIEW_MAX_TOKENS", "2600"))),
)
REPORT_QA_MAX_TOKENS = max(
    700, min(2400, int(os.getenv("INTERVIEW_REPORT_QA_MAX_TOKENS", "1600")))
)
REPORT_TIMEOUT_SECONDS = max(
    15.0, float(os.getenv("INTERVIEW_REPORT_TIMEOUT_SECONDS", "90"))
)
REPORT_OVERVIEW_ATTEMPTS = max(
    2, min(6, int(os.getenv("INTERVIEW_REPORT_OVERVIEW_ATTEMPTS", "6")))
)
REPORT_QA_ATTEMPTS = max(
    1, min(5, int(os.getenv("INTERVIEW_REPORT_QA_ATTEMPTS", "4")))
)
REPORT_CHUNK_MAX_CHARS = max(
    8000, min(32000, int(os.getenv("INTERVIEW_REPORT_CHUNK_MAX_CHARS", "24000")))
)
REPORT_ITEM_MAX_CHARS = max(
    3000, min(9000, int(os.getenv("INTERVIEW_REPORT_ITEM_MAX_CHARS", "6000")))
)
REPORT_OVERVIEW_INPUT_MAX_CHARS = max(
    12000,
    min(48000, int(os.getenv("INTERVIEW_REPORT_OVERVIEW_INPUT_MAX_CHARS", "28000"))),
)
REPORT_MAX_CONCURRENCY = max(
    1, min(4, int(os.getenv("INTERVIEW_REPORT_MAX_CONCURRENCY", "2")))
)
REPORT_CHUNK_MAX_TOKENS = max(
    3000, min(12000, int(os.getenv("INTERVIEW_REPORT_CHUNK_MAX_TOKENS", "8000")))
)


class ReportGenerationError(RuntimeError):
    """The mandatory AI overview could not be generated after all retries."""


def _coerce_overall_100(value: object) -> int:
    """新规则：综合得分 0~100 整数。兼容旧 0~5 字段（*20）。"""
    try:
        score = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    if 0 <= score <= 5:
        return max(0, min(100, score * 20))
    return max(0, min(100, score))


def _coerce_dimension_10(value: object) -> int:
    """新规则：维度得分 0~10 整数。兼容旧 0~5 字段（*2）。"""
    try:
        score = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    if 0 <= score <= 5:
        return max(0, min(10, score * 2))
    return max(0, min(10, score))


def _format_duration_seconds(seconds: float | int | None) -> str:
    """把秒数格式化成「X 分 Y 秒」/「X 秒」的展示文本。面了几分钟就几分钟，不瞎编。"""
    if not seconds or seconds <= 0:
        return ""
    total = int(round(seconds))
    if total < 60:
        return f"{total} 秒"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} 分 {secs} 秒" if secs else f"{minutes} 分钟"
    hours, mins = divmod(minutes, 60)
    if secs:
        return f"{hours} 小时 {mins} 分 {secs} 秒"
    return f"{hours} 小时 {mins} 分钟" if mins else f"{hours} 小时"


def _looks_like_realistic_duration(text: str | None) -> bool:
    """判断 LLM 给的时长是否「真的匹配实际秒数」。

    严格策略：当传入了 `actual_duration_seconds` 时，LLM 的 durationText 必须等于
    `_format_duration_seconds(actual_duration_seconds)` 的输出才视为可信。
    否则（无 actual_duration_seconds）做基本格式校验：含「分/秒/小时」单位、不过 24h。"""
    if not text:
        return False
    value = str(text).strip()
    if not value:
        return False
    # 必须包含中文时间单位（分/秒/小时）
    import re
    if not re.search(r"[分秒小时]", value):
        return False
    # 数字合理性
    nums = re.findall(r"\d+", value)
    if not nums:
        return False
    n = max(int(x) for x in nums)
    if n > 24 * 60:
        return False
    return True


def _duration_text_matches_actual(
    text: str | None, actual_seconds: float | int | None
) -> bool:
    """LLM 的 durationText 是否和 actual_seconds 一致（容忍 ±3s 抖动）。"""
    if not actual_seconds or actual_seconds <= 0:
        return True
    if not text:
        return False
    import re
    nums = re.findall(r"\d+", str(text))
    if not nums:
        return False
    # 提取分/秒
    minutes = 0
    seconds = 0
    # 解析 "X 分 Y 秒" / "X 分钟" / "X 秒"
    minute_match = re.search(r"(\d+)\s*分", str(text))
    second_match = re.search(r"(\d+)\s*秒", str(text))
    if minute_match:
        minutes = int(minute_match.group(1))
    if second_match:
        seconds = int(second_match.group(1))
    if not minute_match and not second_match:
        # 纯数字 + 小时/钟字样：fallback
        return False
    derived = minutes * 60 + seconds
    return abs(derived - int(actual_seconds)) <= 3


class ReportGenerator:
    def __init__(
        self,
        llm_client=None,
        *,
        prompt_template: str | None = None,
        qa_prompt_template: str | None = None,
        context: dict | None = None,
        require_ai_overview: bool = False,
    ) -> None:
        self._llm = llm_client
        self._prompt_template = prompt_template or DEFAULT_REPORT_OVERVIEW_PROMPT
        self._qa_prompt_template = qa_prompt_template or DEFAULT_REPORT_QA_PROMPT
        self._context = dict(context or {})
        self._require_ai_overview = bool(require_ai_overview)
        # Successful AI chunks survive a report retry in the same controller.
        self._qa_chunk_cache: dict[str, dict] = {}
        # Keep the most recent incomplete overview so a later retry can repair it.
        self._overview_repair_cache: dict[str, str] = {}

    @staticmethod
    def _is_non_retryable_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            token in message
            for token in (
                "401",
                "403",
                "unauthorized",
                "forbidden",
                "invalid api key",
                "authentication",
                "model not found",
            )
        )

    async def generate_async(
        self,
        exchanges: list[Exchange],
        *,
        transcript: list[TranscriptTurn] | None = None,
        rubric_dimensions: list[str] | None = None,
        termination_reason: str | None = None,
        actual_duration_seconds: float | int | None = None,
        on_progress: Callable[[dict], Awaitable[None] | None] | None = None,
    ) -> InterviewReport:
        if self._llm is None:
            if self._require_ai_overview:
                raise ReportGenerationError(
                    "AI 报告模型未配置，无法生成综合结论"
                )
            return self.generate(
                exchanges,
                transcript=transcript,
                rubric_dimensions=rubric_dimensions,
                termination_reason=termination_reason,
                actual_duration_seconds=actual_duration_seconds,
            )
        transcript_items = list(transcript or [])
        qa_items = self._build_qa_items(exchanges, transcript_items)
        qa_chunks = self._chunk_qa_items(qa_items)
        total_steps = max(3, len(qa_chunks) + 3)
        progress_snapshot = {
            "stage": "preprocessing",
            "completed_steps": 0,
            "total_steps": total_steps,
            "percent": 0,
        }

        async def emit_monotonic_progress(payload: dict) -> None:
            state = str(payload.get("state") or "generating")
            completed_steps = int(payload.get("completed_steps") or 0)
            reported_total = int(payload.get("total_steps") or total_steps)
            percent = max(0, min(100, int(payload.get("percent") or 0)))
            stage = str(payload.get("stage") or "")
            if state != "completed":
                completed_steps = max(
                    int(progress_snapshot["completed_steps"]), completed_steps
                )
                reported_total = max(
                    int(progress_snapshot["total_steps"]), reported_total
                )
                percent = max(int(progress_snapshot["percent"]), percent)
            if stage in {"", "error"}:
                stage = str(progress_snapshot["stage"] or "preprocessing")
            normalized = {
                **payload,
                "stage": stage,
                "completed_steps": completed_steps,
                "total_steps": reported_total,
                "percent": percent,
            }
            progress_snapshot.update(
                {
                    "stage": stage,
                    "completed_steps": completed_steps,
                    "total_steps": reported_total,
                    "percent": percent,
                }
            )
            await self._emit_progress(on_progress, **normalized)

        await self._emit_progress(
            emit_monotonic_progress,
            state="generating",
            stage="preprocessing",
            message="正在整理面试记录",
            completed_steps=0,
            total_steps=total_steps,
            percent=5,
            attempt=1,
            max_attempts=REPORT_OVERVIEW_ATTEMPTS,
        )
        try:
            await self._emit_progress(
                emit_monotonic_progress,
                state="generating",
                stage="chunk_analysis",
                message=(
                    f"正在分析 {len(qa_chunks)} 组问答"
                    if qa_chunks
                    else "正在核对面试记录"
                ),
                completed_steps=0,
                total_steps=total_steps,
                percent=10,
                attempt=1,
                max_attempts=REPORT_QA_ATTEMPTS,
            )
            chunk_by_index: dict[int, dict] = {}
            qa_chars = 0
            semaphore = asyncio.Semaphore(REPORT_MAX_CONCURRENCY)
            tasks = [
                asyncio.create_task(
                    self._analyze_qa_chunk_with_retries(
                        index,
                        chunk,
                        rubric_dimensions or [],
                        termination_reason,
                        actual_duration_seconds,
                        semaphore=semaphore,
                    )
                )
                for index, chunk in enumerate(qa_chunks)
            ]
            completed_qa = 0
            chunk_errors: list[Exception] = []
            for task in asyncio.as_completed(tasks):
                try:
                    index, result, qa_result = await task
                except Exception as exc:  # keep successful chunks cached for retry
                    chunk_errors.append(exc)
                    continue
                completed_qa += 1
                qa_chars += len(qa_result)
                chunk_by_index[index] = result
                await self._emit_progress(
                    emit_monotonic_progress,
                    state="generating",
                    stage="chunk_analysis",
                    message=f"已完成 {completed_qa}/{len(qa_chunks)} 组问答分析",
                    completed_steps=completed_qa,
                    total_steps=total_steps,
                    percent=min(
                        68,
                        10 + round(completed_qa / max(1, len(qa_chunks)) * 58),
                    ),
                    attempt=1,
                    max_attempts=REPORT_QA_ATTEMPTS,
                )
            if chunk_errors:
                raise chunk_errors[0]
            ordered_chunks = [chunk_by_index[index] for index in range(len(qa_chunks))]
            qa_analyses = self._merge_segment_analyses(ordered_chunks)
            overview_input = self._build_overview_input(ordered_chunks)
            overview_prompt = self._build_overview_prompt(
                overview_input,
                rubric_dimensions or [],
                termination_reason,
                actual_duration_seconds,
            )
            raw, report = await self._generate_overview_with_retries(
                overview_prompt,
                exchanges,
                actual_duration_seconds,
                on_progress=emit_monotonic_progress,
                total_steps=total_steps,
                evidence_ids={str(item["exchangeId"]) for item in qa_items},
            )
            report.qa_analyses = qa_analyses
            report.generation_source = "llm"
            await self._emit_progress(
                emit_monotonic_progress,
                state="generating",
                stage="validating",
                message="正在核验报告完整性",
                completed_steps=total_steps - 1,
                total_steps=total_steps,
                percent=94,
                attempt=1,
                max_attempts=REPORT_OVERVIEW_ATTEMPTS,
            )
            if self._require_ai_overview:
                self._validate_complete_ai_report(report, qa_items)
            await self._emit_progress(
                emit_monotonic_progress,
                state="completed",
                stage="completed",
                message="AI 报告生成完成",
                completed_steps=total_steps,
                total_steps=total_steps,
                percent=100,
                attempt=1,
                max_attempts=REPORT_OVERVIEW_ATTEMPTS,
                generation_source=report.generation_source,
            )
            logger.info(
                "interview report generated by LLM: source=%s overview_chars=%s "
                "qa_chars=%s qa_chunks=%s questions=%s",
                report.generation_source,
                len(raw),
                qa_chars,
                len(qa_chunks),
                len(report.qa_analyses),
            )
            return report
        except Exception as exc:
            logger.exception(
                "interview report mandatory AI overview failed: %s",
                exc,
            )
            if not self._require_ai_overview:
                report = self.generate(
                    exchanges,
                    transcript=transcript,
                    rubric_dimensions=rubric_dimensions,
                    termination_reason=termination_reason,
                    actual_duration_seconds=actual_duration_seconds,
                )
                report.generation_source = "fallback"
                return report
            await self._emit_progress(
                emit_monotonic_progress,
                state="error",
                stage=str(progress_snapshot["stage"]),
                message="AI 报告尚未完整生成，可继续生成",
                completed_steps=int(progress_snapshot["completed_steps"]),
                total_steps=int(progress_snapshot["total_steps"]),
                percent=int(progress_snapshot["percent"]),
                attempt=REPORT_OVERVIEW_ATTEMPTS,
                max_attempts=REPORT_OVERVIEW_ATTEMPTS,
                error=str(exc)[:240],
            )
            if isinstance(exc, ReportGenerationError):
                raise
            raise ReportGenerationError(str(exc)) from exc

    async def _generate_overview_with_retries(
        self,
        prompt: str,
        exchanges: list[Exchange],
        actual_duration_seconds: float | int | None,
        *,
        on_progress,
        total_steps: int,
        evidence_ids: set[str] | None = None,
    ) -> tuple[str, InterviewReport]:
        last_error: Exception | None = None
        cache_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        last_raw = self._overview_repair_cache.get(cache_key, "")
        for attempt in range(1, REPORT_OVERVIEW_ATTEMPTS + 1):
            await self._emit_progress(
                on_progress,
                state="retrying" if attempt > 1 else "generating",
                stage="overview",
                message=(
                    "正在生成 AI 综合结论"
                    if attempt == 1
                    else "AI 服务响应较慢，正在继续处理"
                ),
                completed_steps=max(1, total_steps - 2),
                total_steps=total_steps,
                percent=min(88, 70 + attempt * 3),
                attempt=attempt,
                max_attempts=REPORT_OVERVIEW_ATTEMPTS,
            )
            try:
                raw = ""
                request_prompt = prompt
                if last_error is not None:
                    request_prompt += (
                        "\n\n上一次输出未通过完整性校验。请保留其中正确内容，只修复缺失或错误字段，"
                        "然后重新输出一份完整 JSON。"
                        f"\n校验错误：{str(last_error)[:400]}"
                    )
                if last_raw:
                    request_prompt += "\n上一次待修复 JSON：\n" + last_raw[:16000]
                raw = await self._request_json(
                    request_prompt, max_tokens=REPORT_OVERVIEW_MAX_TOKENS
                )
                data = self._load_json_object(raw)
                self._validate_overview_evidence_refs(data, evidence_ids or set())
                report = self._parse_llm_report(
                    raw, exchanges, actual_duration_seconds
                )
                if self._require_ai_overview:
                    self._validate_ai_overview_sections(report)
                self._overview_repair_cache.pop(cache_key, None)
                return raw, report
            except Exception as exc:  # noqa: BLE001 - mandatory structured retry
                last_error = exc
                if raw:
                    last_raw = raw
                    self._overview_repair_cache[cache_key] = raw
                logger.warning(
                    "report overview attempt %s/%s failed: %s",
                    attempt,
                    REPORT_OVERVIEW_ATTEMPTS,
                    exc,
                )
                if self._is_non_retryable_error(exc):
                    break
                if attempt < REPORT_OVERVIEW_ATTEMPTS:
                    await asyncio.sleep(min(2.0, 0.5 * attempt))
        raise ReportGenerationError(
            f"AI 综合结论在 {REPORT_OVERVIEW_ATTEMPTS} 次尝试后仍失败：{last_error}"
        )

    @staticmethod
    def _validate_overview_evidence_refs(data: dict, allowed: set[str]) -> None:
        if not allowed:
            return
        refs = data.get("evidenceRefs") or data.get("evidence_refs") or []
        if not isinstance(refs, list) or not refs:
            raise ValueError("AI overview is missing evidenceRefs")
        normalized = {str(value) for value in refs if str(value)}
        if not normalized.issubset(allowed):
            raise ValueError("AI overview cited an unknown exchangeId")

    @staticmethod
    def _split_report_text(text: str, limit: int = REPORT_ITEM_MAX_CHARS) -> list[str]:
        value = " ".join(str(text or "").split())
        if not value:
            return [""]
        return [value[start : start + limit] for start in range(0, len(value), limit)]

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
        other = max(0, len(text) - cjk)
        return cjk + (other + 3) // 4

    @staticmethod
    def _evaluation_payload(exchange: Exchange) -> dict:
        evaluation = exchange.evaluation
        if evaluation is None:
            return {}
        return {
            "score": evaluation.score,
            "dimensions": dict(evaluation.dimensions),
            "strengths": list(evaluation.strengths[:4]),
            "weaknesses": list(evaluation.weaknesses[:4]),
        }

    def _build_qa_items(
        self,
        exchanges: list[Exchange],
        transcript: list[TranscriptTurn],
    ) -> list[dict]:
        items: list[dict] = []
        seen: set[tuple[str, str]] = set()
        order = 0
        if exchanges:
            for exchange in exchanges:
                question = " ".join(str(exchange.prompt_text or "").split())
                answer = " ".join(str(exchange.answer_text or "").split())
                if not question or not answer:
                    continue
                signature = (question, answer)
                if signature in seen:
                    continue
                seen.add(signature)
                order += 1
                parts = self._split_report_text(answer)
                for segment_index, part in enumerate(parts, start=1):
                    items.append(
                        {
                            "order": order,
                            "questionId": exchange.question_id,
                            "exchangeId": exchange.exchange_id,
                            "segmentIndex": segment_index,
                            "segmentCount": len(parts),
                            "promptType": exchange.prompt_type,
                            "parentExchangeId": exchange.parent_exchange_id or "",
                            "question": question[:1200],
                            "answer": part,
                            "evaluation": self._evaluation_payload(exchange),
                        }
                    )
            return items

        current_question = ""
        current_exchange = ""
        for turn in transcript:
            if turn.role == "interviewer" and turn.type in {
                "main_question",
                "follow_up",
                "question",
                "self_intro",
            }:
                current_question = " ".join(str(turn.text or "").split())
                current_exchange = str(turn.exchange_id or turn.turn_id)
                continue
            if turn.role != "candidate" or turn.type != "answer" or not turn.text:
                continue
            question = current_question or "请结合本场面试说明你的相关经历。"
            exchange_id = str(turn.exchange_id or current_exchange or turn.turn_id)
            answer = " ".join(str(turn.text).split())
            signature = (question, answer)
            if signature in seen:
                continue
            seen.add(signature)
            order += 1
            parts = self._split_report_text(answer)
            for segment_index, part in enumerate(parts, start=1):
                items.append(
                    {
                        "order": order,
                        "questionId": str(turn.question_id or ""),
                        "exchangeId": exchange_id,
                        "segmentIndex": segment_index,
                        "segmentCount": len(parts),
                        "promptType": "answer",
                        "parentExchangeId": "",
                        "question": question[:1200],
                        "answer": part,
                        "evaluation": {},
                    }
                )
        return items

    @staticmethod
    def _chunk_qa_items(items: list[dict]) -> list[list[dict]]:
        chunks: list[list[dict]] = []
        current: list[dict] = []
        current_chars = 2
        for item in items:
            item_chars = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
            candidate = current + [item]
            candidate_json = json.dumps(
                candidate, ensure_ascii=False, separators=(",", ":")
            )
            if current and (
                current_chars + item_chars > REPORT_CHUNK_MAX_CHARS
                or ReportGenerator._estimate_tokens(candidate_json)
                > REPORT_CHUNK_MAX_TOKENS
                or len(current) >= 3
            ):
                chunks.append(current)
                current = []
                current_chars = 2
            current.append(item)
            current_chars += item_chars
        if current:
            chunks.append(current)
        return chunks

    def _build_qa_chunk_prompt(
        self,
        chunk: list[dict],
        rubric_dimensions: list[str],
        termination_reason: str | None,
        actual_duration_seconds: float | int | None,
    ) -> str:
        payload = json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))
        base = render_template(
            self._qa_prompt_template,
            {
                **self._context,
                "rubric_dimensions": json.dumps(rubric_dimensions, ensure_ascii=False),
                "termination_reason": termination_reason or "",
                "actual_duration_seconds": _format_duration_seconds(actual_duration_seconds)
                or "未知",
                "transcript": payload,
            },
        )
        return (
            f"{base}\n\n"
            "本次输入是经过清洗的问答单元。必须为输入数组中的每一项输出一条 qaAnalyses，"
            "顺序完全一致，不得遗漏。每条必须原样返回 exchangeId 和 segmentIndex。\n"
            "同时输出 chunkSummary（240字内）、strengths、risks、dimensionEvidence。\n"
            '固定结构：{"chunkSummary":"","strengths":[],"risks":[],"dimensionEvidence":{},'
            '"qaAnalyses":[{"exchangeId":"","segmentIndex":1,"questionIndex":1,'
            '"question":"","answer":"候选人关键回答，200字内","strengths":[],"risks":[],'
            '"commentary":"【面试官点评】...\\n\\n【参考思路】..."}]}。'
        )

    async def _analyze_qa_chunk_with_retries(
        self,
        index: int,
        chunk: list[dict],
        rubric_dimensions: list[str],
        termination_reason: str | None,
        actual_duration_seconds: float | int | None,
        *,
        semaphore: asyncio.Semaphore,
    ) -> tuple[int, dict, str]:
        prompt = self._build_qa_chunk_prompt(
            chunk,
            rubric_dimensions,
            termination_reason,
            actual_duration_seconds,
        )
        cache_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        cached = self._qa_chunk_cache.get(cache_key)
        if cached is not None:
            return index, cached, ""
        last_error: Exception | None = None
        for attempt in range(1, REPORT_QA_ATTEMPTS + 1):
            try:
                request_prompt = prompt
                if last_error is not None:
                    request_prompt += (
                        "\n\n上一次输出未覆盖全部问答或 JSON 不合法。"
                        f"请按固定结构完整重写。校验错误：{str(last_error)[:240]}"
                    )
                async with semaphore:
                    raw = await self._request_json(
                        request_prompt, max_tokens=REPORT_QA_MAX_TOKENS
                    )
                data = self._load_json_object(raw)
                result = self._validate_qa_chunk_result(data, chunk)
                self._qa_chunk_cache[cache_key] = result
                return index, result, raw
            except Exception as exc:  # noqa: BLE001 - structured retry
                last_error = exc
                logger.warning(
                    "report QA chunk %s attempt %s/%s failed: %s",
                    index + 1,
                    attempt,
                    REPORT_QA_ATTEMPTS,
                    exc,
                )
                if self._is_non_retryable_error(exc):
                    break
                if attempt < REPORT_QA_ATTEMPTS:
                    await asyncio.sleep(min(1.2, 0.3 * attempt))
        raise ReportGenerationError(
            f"第 {index + 1} 组问答在多次 AI 分析后仍失败：{last_error}"
        )

    @classmethod
    def _validate_qa_chunk_result(cls, data: dict, expected: list[dict]) -> dict:
        raw_analyses = data.get("qaAnalyses") or data.get("qa_analyses") or []
        if not isinstance(raw_analyses, list) or len(raw_analyses) != len(expected):
            raise ValueError("AI QA chunk did not cover every input item")
        analyses: list[dict] = []
        for position, (raw_item, expected_item) in enumerate(
            zip(raw_analyses, expected), start=1
        ):
            if not isinstance(raw_item, dict):
                raise ValueError("AI QA analysis item must be an object")
            exchange_id = str(raw_item.get("exchangeId") or expected_item["exchangeId"])
            segment_index = int(
                raw_item.get("segmentIndex") or expected_item["segmentIndex"]
            )
            if exchange_id != expected_item["exchangeId"]:
                raise ValueError("AI QA analysis returned a mismatched exchangeId")
            if segment_index != expected_item["segmentIndex"]:
                raise ValueError("AI QA analysis returned a mismatched segmentIndex")
            normalized_item = dict(raw_item)
            normalized_item.setdefault("questionIndex", int(expected_item["order"]))
            normalized_item.setdefault("question", str(expected_item["question"]))
            normalized_item.setdefault("answer", str(expected_item["answer"])[:200])
            parsed = cls._parse_qa_analyses({"qaAnalyses": [normalized_item]})
            if not parsed or len(parsed[0].commentary.strip()) < 8:
                raise ValueError("AI QA analysis commentary is missing")
            parsed[0].question_index = int(expected_item["order"])
            analyses.append(
                {
                    "exchangeId": exchange_id,
                    "segmentIndex": segment_index,
                    "order": int(expected_item["order"]),
                    "analysis": parsed[0],
                }
            )
        summary = str(data.get("chunkSummary") or "").strip()
        if not summary:
            summary = "；".join(
                item["analysis"].commentary for item in analyses
            )[:500]
        if len(summary) < 8:
            raise ValueError("AI QA chunk summary is missing")
        return {
            "chunkSummary": summary[:600],
            "strengths": [str(v)[:160] for v in (data.get("strengths") or [])[:6]],
            "risks": [str(v)[:160] for v in (data.get("risks") or [])[:6]],
            "dimensionEvidence": data.get("dimensionEvidence")
            if isinstance(data.get("dimensionEvidence"), dict)
            else {},
            "exchangeIds": [str(item["exchangeId"]) for item in expected],
            "analyses": analyses,
        }

    @staticmethod
    def _unique_text(values: list[str], *, limit: int = 8) -> list[str]:
        result: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result

    @classmethod
    def _merge_segment_analyses(cls, chunks: list[dict]) -> list[ReportQaAnalysis]:
        grouped: dict[str, dict] = {}
        for chunk in chunks:
            for item in chunk.get("analyses") or []:
                exchange_id = str(item["exchangeId"])
                analysis: ReportQaAnalysis = item["analysis"]
                target = grouped.setdefault(
                    exchange_id,
                    {
                        "order": int(item["order"]),
                        "question": analysis.question,
                        "answers": [],
                        "strengths": [],
                        "risks": [],
                        "commentaries": [],
                        "approach": [],
                        "reference": [],
                    },
                )
                target["answers"].append(analysis.answer)
                target["strengths"].extend(analysis.strengths)
                target["risks"].extend(analysis.risks)
                target["commentaries"].append(analysis.commentary)
                target["approach"].extend(analysis.approach)
                if analysis.reference_answer:
                    target["reference"].append(analysis.reference_answer)
        merged: list[ReportQaAnalysis] = []
        for value in sorted(grouped.values(), key=lambda item: item["order"]):
            merged.append(
                ReportQaAnalysis(
                    question_index=len(merged) + 1,
                    question=value["question"],
                    answer="\n".join(cls._unique_text(value["answers"], limit=20))[:3000],
                    strengths=cls._unique_text(value["strengths"]),
                    risks=cls._unique_text(value["risks"]),
                    commentary="\n\n".join(
                        cls._unique_text(value["commentaries"], limit=20)
                    )[:4000],
                    approach=cls._unique_text(value["approach"]),
                    reference_answer="\n".join(
                        cls._unique_text(value["reference"], limit=8)
                    )[:1600],
                )
            )
        return merged

    @staticmethod
    def _build_overview_input(chunks: list[dict]) -> str:
        compact = {
            "source": "ai_chunk_analyses",
            "chunks": [
                {
                    "index": index + 1,
                    "exchangeIds": chunk.get("exchangeIds") or [],
                    "summary": str(chunk.get("chunkSummary") or "")[:600],
                    "strengths": (chunk.get("strengths") or [])[:5],
                    "risks": (chunk.get("risks") or [])[:5],
                    "dimensionEvidence": chunk.get("dimensionEvidence") or {},
                }
                for index, chunk in enumerate(chunks)
            ],
        }
        text = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        if (
            len(text) <= REPORT_OVERVIEW_INPUT_MAX_CHARS
            and ReportGenerator._estimate_tokens(text) <= REPORT_CHUNK_MAX_TOKENS
        ):
            return text
        for chunk in compact["chunks"]:
            chunk["summary"] = str(chunk["summary"])[:240]
            chunk["strengths"] = [str(v)[:80] for v in chunk["strengths"][:3]]
            chunk["risks"] = [str(v)[:80] for v in chunk["risks"][:3]]
            chunk["dimensionEvidence"] = {}
        text = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        if (
            len(text) > REPORT_OVERVIEW_INPUT_MAX_CHARS
            or ReportGenerator._estimate_tokens(text) > REPORT_CHUNK_MAX_TOKENS
        ):
            raise ReportGenerationError("AI 分块摘要仍超过综合结论输入预算")
        return text

    def _build_overview_prompt(
        self,
        overview_input: str,
        rubric_dimensions: list[str],
        termination_reason: str | None,
        actual_duration_seconds: float | int | None,
    ) -> str:
        base = render_template(
            self._prompt_template,
            {
                **self._context,
                "rubric_dimensions": json.dumps(rubric_dimensions, ensure_ascii=False),
                "termination_reason": termination_reason or "",
                "actual_duration_seconds": _format_duration_seconds(actual_duration_seconds)
                or "未知",
                "transcript": overview_input,
            },
        )
        return (
            f"{base}\n\n"
            "注意：上面的 transcript 字段是已经由 AI 完成的分块分析摘要，不是原始对话。"
            "综合结论必须只引用其中存在的证据，不得补写未出现的项目、数据或工具。"
            "输出 JSON 顶层必须包含 evidenceRefs 数组，且只能填写输入中真实存在的 exchangeIds。"
            "完整报告还必须包含 highlights、dimensionCommentaries，以及包含立即行动、短期提升、"
            "中期规划三个阶段的 learningPlan；缺少任一部分都会被判定为生成失败。"
        )

    @staticmethod
    def _validate_ai_overview_sections(report: InterviewReport) -> None:
        if not report.summary or not report.dimension_scores:
            raise ReportGenerationError("AI 综合结论缺少总评或维度分析")
        if not report.strengths or not report.weaknesses or not report.recommendations:
            raise ReportGenerationError("AI 综合结论缺少优势、风险或建议")
        if not report.highlights.alerts or not report.highlights.advice:
            raise ReportGenerationError("AI 综合结论缺少重点提醒")
        if not report.dimension_commentaries:
            raise ReportGenerationError("AI 综合结论缺少维度点评")
        if len(report.learning_plan.phases) < 3 or any(
            not phase.title or not phase.items
            for phase in report.learning_plan.phases[:3]
        ):
            raise ReportGenerationError("AI 综合结论缺少学习计划")

    @classmethod
    def _validate_complete_ai_report(
        cls, report: InterviewReport, qa_items: list[dict]
    ) -> None:
        cls._validate_ai_overview_sections(report)
        if qa_items and len(report.qa_analyses) != len(
            {str(item["exchangeId"]) for item in qa_items}
        ):
            raise ReportGenerationError("AI 逐题分析未完整覆盖全部有效回答")

    async def _generate_qa_chunk_with_retries(
        self,
        index: int,
        chunk: list[TranscriptTurn],
        rubric_dimensions: list[str],
        termination_reason: str | None,
        actual_duration_seconds: float | int | None,
    ) -> tuple[int, list[TranscriptTurn], str, Exception | None]:
        prompt = self._build_llm_prompt(
            chunk,
            rubric_dimensions,
            termination_reason,
            actual_duration_seconds,
            template=self._qa_prompt_template,
        )
        last_error: Exception | None = None
        for attempt in range(1, REPORT_QA_ATTEMPTS + 1):
            try:
                raw = await self._request_json(
                    prompt, max_tokens=REPORT_QA_MAX_TOKENS
                )
                analyses = self._parse_qa_analyses(self._load_json_object(raw))
                if not analyses and chunk:
                    raise ValueError("LLM QA report contains no analyses")
                return index, chunk, raw, None
            except Exception as exc:  # noqa: BLE001 - partial section may retry/fallback
                last_error = exc
                if attempt < REPORT_QA_ATTEMPTS:
                    await asyncio.sleep(min(1.2, 0.3 * attempt))
        return index, chunk, "", last_error

    @staticmethod
    async def _emit_progress(callback, **payload) -> None:
        if callback is None:
            return
        result = callback(payload)
        if inspect.isawaitable(result):
            await result

    async def _request_json(self, prompt: str, *, max_tokens: int) -> str:
        generate_json = getattr(self._llm, "generate_json_once", None)
        if callable(generate_json):
            return await asyncio.wait_for(
                generate_json(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=0.2,
                ),
                timeout=REPORT_TIMEOUT_SECONDS,
            )
        return await asyncio.wait_for(
            self._llm.generate_once(prompt, max_tokens=max_tokens),
            timeout=REPORT_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _chunk_transcript_for_qa(
        transcript: list[TranscriptTurn], *, answers_per_chunk: int = 2
    ) -> list[list[TranscriptTurn]]:
        """Keep each QA request short enough for low-latency structured output."""
        if not transcript:
            return []
        chunks: list[list[TranscriptTurn]] = []
        current: list[TranscriptTurn] = []
        answers = 0
        for turn in transcript:
            if (
                current
                and answers >= answers_per_chunk
                and turn.role == "interviewer"
            ):
                chunks.append(current)
                current = []
                answers = 0
            current.append(turn)
            if turn.role == "candidate" and turn.type == "answer":
                answers += 1
        if current:
            chunks.append(current)
        return chunks

    def generate(
        self,
        exchanges: list[Exchange],
        *,
        transcript: list[TranscriptTurn] | None = None,
        rubric_dimensions: list[str] | None = None,
        termination_reason: str | None = None,
        actual_duration_seconds: float | int | None = None,
    ) -> InterviewReport:
        transcript = list(transcript or [])
        scored = [item.evaluation for item in exchanges if item.evaluation is not None]
        # 兜底综合分：0~100 整数；旧 0~5 / 0~10 输入都会被 _coerce_overall_100 自动归一
        if scored:
            avg5 = sum(item.score for item in scored) / len(scored)
            overall = _coerce_overall_100(avg5)
        else:
            overall = 0
        strengths: list[str] = []
        weaknesses: list[str] = []
        for evaluation in scored:
            strengths.extend(evaluation.strengths)
            weaknesses.extend(evaluation.weaknesses)
        candidate_answers = [
            turn for turn in transcript if turn.role == "candidate" and turn.type == "answer"
        ]
        skipped = [turn for turn in transcript if turn.type == "question_skipped"]
        dimension_scores = self._dimension_scores(
            rubric_dimensions or [],
            scored,
            candidate_answers,
            skipped,
        )
        if not strengths and candidate_answers:
            strengths.append("候选人提供了可用于评估的回答。")
        if skipped:
            weaknesses.append(f"有 {len(skipped)} 个问题未收到有效回答，整体信息量不足以做完整判断。")
        recommendations = ["继续补充具体案例、量化结果和真实的技术/业务取舍。"]
        if termination_reason:
            recommendations.append(f"本次面试提前结束原因：{termination_reason}。")
        # 兜底总评：写得像资深面试官对候选人的私下点评，不要客套
        skipped_count = len(skipped)
        answered_count = len(candidate_answers)
        if answered_count == 0:
            summary = (
                "候选人本场未给出任何有效回答，整体信息量不足以做出评估，"
                "态度比较敷衍，建议下次面试前先准备项目经历和岗位核心题。"
            )
        elif skipped_count >= max(1, answered_count):
            summary = (
                f"候选人本场只回答了 {answered_count} 道题，跳过了 {skipped_count} 道关键问题，"
                f"信息密度偏低、配合度不足；建议下次重点补齐项目经验和岗位核心能力。"
            )
        elif overall >= 80:
            summary = (
                f"候选人本场整体表现稳定且有亮点，回答了 {answered_count} 道题，"
                f"能讲清关键背景、判断依据和结果；如果继续加强个别维度的深度，整体竞争力会更强。"
            )
        elif overall >= 60:
            summary = (
                f"候选人本场有 {answered_count} 道有效回答，基础能力可看出，但关键回答偏结论先行，"
                f"缺少完整的推导过程和结果数据；建议补强项目经历的细节和复盘视角。"
            )
        else:
            summary = (
                f"候选人本场回答了 {answered_count} 道题，但回答信息量偏少、缺乏证据和闭环，"
                f"暂不足以支撑对目标岗位的有效判断；建议先按岗位核心题整理 3-5 个真实案例再来面试。"
            )
        cover = self._build_cover(score=overall, actual_duration_seconds=actual_duration_seconds)
        highlights = self._build_highlights(weaknesses, recommendations)
        dimension_commentaries = self._build_dimension_commentaries(dimension_scores)
        learning_plan = self._build_learning_plan(
            weaknesses, recommendations, dimension_commentaries
        )
        qa_analyses = self._build_qa_analyses(transcript)
        return InterviewReport(
            summary=summary,
            overall_score=overall,
            strengths=strengths,
            weaknesses=weaknesses,
            recommendations=recommendations,
            exchanges=list(exchanges),
            dimension_scores=dimension_scores,
            cover=cover,
            highlights=highlights,
            dimension_commentaries=dimension_commentaries,
            learning_plan=learning_plan,
            qa_analyses=qa_analyses,
        )

    def _build_llm_prompt(
        self,
        transcript: list[TranscriptTurn],
        rubric_dimensions: list[str],
        termination_reason: str | None,
        actual_duration_seconds: float | int | None = None,
        *,
        template: str | None = None,
    ) -> str:
        turns = [
            {
                "role": turn.role,
                "type": turn.type,
                "text": turn.text,
                "questionId": turn.question_id,
                "exchangeId": turn.exchange_id,
                "metadata": turn.metadata,
            }
            for turn in transcript
        ]
        return render_template(
            template or self._prompt_template,
            {
                **self._context,
                "rubric_dimensions": json.dumps(rubric_dimensions, ensure_ascii=False),
                "termination_reason": termination_reason or "",
                "actual_duration_seconds": _format_duration_seconds(actual_duration_seconds) or "未知",
                "transcript": json.dumps(turns, ensure_ascii=False),
            },
        )

    def _parse_llm_report(
        self,
        raw: str,
        exchanges: list[Exchange],
        actual_duration_seconds: float | int | None = None,
    ) -> InterviewReport:
        data = ReportGenerator._load_json_object(raw)
        ReportGenerator._validate_llm_report(data)
        dimensions = {}
        for name, value in (data.get("dimensions") or {}).items():
            if not isinstance(value, dict):
                continue
            evidence = value.get("evidence") or []
            concerns = value.get("concerns") or value.get("concern") or []
            recommendations = value.get("recommendations") or []
            if isinstance(evidence, str):
                evidence = [evidence] if evidence.strip() else []
            if isinstance(concerns, str):
                concerns = [concerns] if concerns.strip() else []
            if isinstance(recommendations, str):
                recommendations = (
                    [recommendations] if recommendations.strip() else []
                )
            dimensions[str(name)] = DimensionAssessment(
                score=_coerce_dimension_10(value.get("score")),
                evidence=[str(item) for item in evidence],
                concerns=[str(item) for item in concerns],
                recommendations=[str(item) for item in recommendations],
                confidence=str(value.get("confidence") or "low"),
            )
        # 兜底：检测 LLM 是否「默认全部相同高分」(典型症状：5 个维度都给 7 或 8)。
        # 一旦发现，用每个 exchange 真实 evaluation 的维度分重新计算。
        if ReportGenerator._looks_like_default_dimension_scoring(dimensions):
            fallback = ReportGenerator._fallback_dimension_scores(
                exchanges, dimensions
            )
            for key, value in fallback.items():
                if key in dimensions and value is not None:
                    dimensions[key] = value
        cover_data = data.get("cover") or {}
        highlights_data = data.get("highlights") or {}
        learning_plan_data = data.get("learningPlan") or data.get("learning_plan") or {}
        dimension_commentaries = []
        for item in data.get("dimensionCommentaries") or data.get("dimension_commentaries") or []:
            if not isinstance(item, dict):
                continue
            dimension_commentaries.append(
                ReportDimensionCommentary(
                    key=str(item.get("key") or ""),
                    title=str(item.get("title") or ""),
                    score=int(item.get("score") or 0),
                    commentary=str(item.get("commentary") or ""),
                )
            )
        learning_phases = []
        for item in learning_plan_data.get("phases") or []:
            if not isinstance(item, dict):
                continue
            learning_phases.append(
                ReportLearningPhase(
                    title=str(item.get("title") or ""),
                    window=str(item.get("window") or ""),
                    items=[str(v) for v in item.get("items") or []],
                )
            )
        qa_analyses = ReportGenerator._parse_qa_analyses(data)
        # 新规则：综合分 0~100，维度分 0~10；旧字段（0~5 / 0~5）由调用方做兜底换算
        overall_score = _coerce_overall_100(data.get("overallScore") or data.get("overall_score") or 0)
        legacy_cover_score = _coerce_overall_100(cover_data.get("score"))
        if not overall_score and legacy_cover_score:
            overall_score = legacy_cover_score
        # 封面不是分析结论，统一由后端用可信运行时数据生成。旧模型若仍返回
        # cover.score，只用于兼容推导 overallScore，不采信其标题、时长或时间。
        cover = self._build_cover(
            score=overall_score,
            actual_duration_seconds=actual_duration_seconds,
        )
        return InterviewReport(
            summary=str(data.get("summary") or ""),
            overall_score=overall_score,
            strengths=[str(item) for item in data.get("strengths") or []],
            weaknesses=[str(item) for item in data.get("weaknesses") or []],
            recommendations=[str(item) for item in data.get("recommendations") or []],
            exchanges=list(exchanges),
            dimension_scores=dimensions,
            cover=cover,
            highlights=ReportHighlightBlock(
                alerts=[str(item) for item in highlights_data.get("alerts") or []],
                advice=[str(item) for item in highlights_data.get("advice") or []],
            ),
            dimension_commentaries=dimension_commentaries,
            learning_plan=ReportLearningPlan(
                tags=[str(item) for item in learning_plan_data.get("tags") or []],
                phases=learning_phases,
            ),
            qa_analyses=qa_analyses,
            generation_source="llm",
        )

    @staticmethod
    def _parse_qa_analyses(data: dict) -> list[ReportQaAnalysis]:
        qa_analyses: list[ReportQaAnalysis] = []
        for item in data.get("qaAnalyses") or data.get("qa_analyses") or []:
            if not isinstance(item, dict):
                continue
            qa_analyses.append(
                ReportQaAnalysis(
                    question_index=int(item.get("questionIndex") or item.get("question_index") or 0),
                    question=str(item.get("question") or ""),
                    answer=str(item.get("answer") or ""),
                    strengths=[str(v) for v in item.get("strengths") or []],
                    risks=[str(v) for v in item.get("risks") or []],
                    commentary=str(item.get("commentary") or ""),
                )
            )
        return qa_analyses

    @staticmethod
    def _load_json_object(raw: str) -> dict:
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0].strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise
            data = json.loads(text[start : end + 1])
        if not isinstance(data, dict):
            raise ValueError("LLM report must be a JSON object")
        return data

    @staticmethod
    def _validate_llm_report(data: dict) -> None:
        summary = str(data.get("summary") or "").strip()
        if len(summary) < 12:
            raise ValueError("LLM report summary is missing or too short")
        dimensions = data.get("dimensions")
        if not isinstance(dimensions, dict) or not dimensions:
            raise ValueError("LLM report is missing dimension analysis")
        if not isinstance(data.get("strengths"), list):
            raise ValueError("LLM report strengths must be a list")
        if not isinstance(data.get("weaknesses"), list):
            raise ValueError("LLM report weaknesses must be a list")

    @staticmethod
    def _looks_like_default_dimension_scoring(
        dimensions: dict[str, DimensionAssessment],
    ) -> bool:
        """LLM 是否把每个维度都给了相同的高分（典型默认 7/8 行为）。

        判定规则（任一满足即视为「全默认」）：
        - 维度数 ≥ 3，且 ≥80% 的维度分 ≥ 5，且所有维度分完全相同；
        - 或：5 个核心维度（5 个 key 都在）的分差 ≤ 1 且均值 ≥ 7。
        """
        if not dimensions:
            return False
        scores = [assessment.score for assessment in dimensions.values()]
        if len(scores) < 3:
            return False
        all_same = len(set(scores)) == 1 and scores[0] >= 5
        if all_same:
            return True
        # 5 维度分差 ≤ 1 且均值 ≥ 7：典型的「全部默认 7/8」
        if len(scores) >= 5 and (max(scores) - min(scores)) <= 1 and (sum(scores) / len(scores)) >= 7:
            return True
        return False

    @staticmethod
    def _fallback_dimension_scores(
        exchanges: list[Exchange],
        existing: dict[str, DimensionAssessment],
    ) -> dict[str, DimensionAssessment | None]:
        """当 LLM 给维度全默认分时，用每个 exchange 的 evaluation 真实维度分重新计算。
        保留 LLM 提供的 evidence / concerns / recommendations 文本，只重算 score。
        """
        result: dict[str, DimensionAssessment | None] = {}
        scored = [item.evaluation for item in exchanges if item.evaluation is not None]
        for dim_name, existing_assessment in existing.items():
            collected = [
                evaluation.dimension_assessments[dim_name]
                for evaluation in scored
                if dim_name in evaluation.dimension_assessments
            ]
            numeric = [
                evaluation.dimensions[dim_name]
                for evaluation in scored
                if dim_name in evaluation.dimensions
            ]
            if collected:
                # 算术平均：5/4 → 4.5 用 0.5 上取整（不要用 banker's rounding）。
                # 注意：average 出来的结果一定在 0~10 范围（来自 evaluation.dimensions），
                # 不需要再 *2 兼容。
                score = int(sum(item.score for item in collected) / len(collected) + 0.5)
                # 复用已有 evidence / concerns / recommendations，只覆盖 score
                result[dim_name] = DimensionAssessment(
                    score=max(0, min(10, score)),
                    evidence=list(existing_assessment.evidence) or [
                        item for sub in collected for item in sub.evidence
                    ],
                    concerns=list(existing_assessment.concerns)
                    or [item for sub in collected for item in sub.concerns],
                    recommendations=list(existing_assessment.recommendations)
                    or [item for sub in collected for item in sub.recommendations],
                    confidence=existing_assessment.confidence or "medium",
                )
            elif numeric:
                result[dim_name] = DimensionAssessment(
                    score=max(0, min(10, int(sum(numeric) / len(numeric) + 0.5))),
                    evidence=list(existing_assessment.evidence),
                    concerns=list(existing_assessment.concerns)
                    or ["回答样本不足，维度证据有限。"],
                    recommendations=list(existing_assessment.recommendations),
                    confidence=existing_assessment.confidence or "low",
                )
            else:
                result[dim_name] = None
        return result

    @staticmethod
    def _dimension_scores(
        rubric_dimensions: list[str],
        scored,
        candidate_answers: list[TranscriptTurn],
        skipped: list[TranscriptTurn],
    ) -> dict[str, DimensionAssessment]:
        result: dict[str, DimensionAssessment] = {}
        for dimension in rubric_dimensions:
            collected = [
                evaluation.dimension_assessments[dimension]
                for evaluation in scored
                if dimension in evaluation.dimension_assessments
            ]
            numeric = [
                evaluation.dimensions[dimension]
                for evaluation in scored
                if dimension in evaluation.dimensions
            ]
            if collected:
                evidence = [
                    item
                    for assessment in collected
                    for item in assessment.evidence
                ]
                concerns = [
                    item
                    for assessment in collected
                    for item in assessment.concerns
                ]
                recommendations = [
                    item
                    for assessment in collected
                    for item in assessment.recommendations
                ]
                score = round(sum(item.score for item in collected) / len(collected))
                confidence = "high" if len(candidate_answers) >= 2 else "medium"
            else:
                evidence = [candidate_answers[0].text] if candidate_answers else []
                concerns = ["回答样本较少，维度证据不足。"] if not evidence else []
                recommendations = ["继续围绕该维度追问具体案例。"]
                score = round(sum(numeric) / len(numeric)) if numeric else 0
                confidence = "medium" if evidence else "low"
            if skipped:
                concerns.append(f"存在 {len(skipped)} 个跳过问题，影响该维度置信度。")
            result[dimension] = DimensionAssessment(
                score=score,
                evidence=evidence,
                concerns=concerns,
                recommendations=recommendations,
                confidence=confidence,
            )
        return result

    def _build_cover(
        self, score: int = 0, actual_duration_seconds: float | int | None = None
    ) -> ReportCover:
        role = str(self._context.get("target_role") or "候选人").strip()
        return ReportCover(
            title=f"{role}模拟面试报告" if role else "模拟面试报告",
            interview_type="综合面试",
            score=int(score or 0),
            duration_text=_format_duration_seconds(actual_duration_seconds),
            generated_at=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        )

    @staticmethod
    def _build_highlights(
        weaknesses: list[str], recommendations: list[str]
    ) -> ReportHighlightBlock:
        return ReportHighlightBlock(
            alerts=list(weaknesses[:3]),
            advice=list(recommendations[:3]),
        )

    @staticmethod
    def _build_dimension_commentaries(
        dimension_scores: dict[str, DimensionAssessment],
    ) -> list[ReportDimensionCommentary]:
        title_map = {
            "communication_clarity": "表达能力",
            "problem_solving": "逻辑能力",
            "outcome_orientation": "结果导向",
            "project_execution": "项目展现力",
            "role_alignment": "岗位契合度",
        }
        commentaries: list[ReportDimensionCommentary] = []
        for key in (
            "communication_clarity",
            "problem_solving",
            "outcome_orientation",
            "project_execution",
            "role_alignment",
        ):
            assessment = dimension_scores.get(key) or DimensionAssessment(score=0)
            text = ""
            if assessment.evidence:
                text = assessment.evidence[0]
            elif assessment.concerns:
                text = assessment.concerns[0]
            elif assessment.recommendations:
                text = assessment.recommendations[0]
            else:
                text = "当前回答样本有限，建议继续补充更完整的案例与论证。"
            commentaries.append(
                ReportDimensionCommentary(
                    key=key,
                    title=title_map.get(key, key),
                    score=_coerce_dimension_10(assessment.score or 0),
                    commentary=text,
                )
            )
        return commentaries

    @staticmethod
    def _build_learning_plan(
        weaknesses: list[str],
        recommendations: list[str],
        dimension_commentaries: list[ReportDimensionCommentary],
    ) -> ReportLearningPlan:
        weakest = sorted(dimension_commentaries, key=lambda item: item.score)[0] if dimension_commentaries else None
        tags = [item for item in (weaknesses[:2] + recommendations[:2]) if item][:4]
        phases = [
            ReportLearningPhase(
                title="立即行动",
                window="1-2周",
                items=[
                    f"围绕“{weaknesses[0]}”整理 2 到 3 个可复用回答模板。"
                    if weaknesses
                    else "优先整理近期最有代表性的 2 到 3 个项目案例。",
                    f"针对{weakest.title}补充更完整的真实案例与结果数据。"
                    if weakest
                    else "补充与目标岗位最相关的高质量项目案例。",
                ],
            ),
            ReportLearningPhase(
                title="短期提升",
                window="1个月",
                items=[
                    recommendations[0]
                    if recommendations
                    else "按背景、目标、动作、结果、复盘的结构反复练习重点题型。",
                    "每周完整模拟一场面试，并复盘回答中的跳跃、空泛和缺证据问题。",
                ],
            ),
            ReportLearningPhase(
                title="中期规划",
                window="2-3个月",
                items=[
                    "沉淀一套稳定的代表项目讲述模板，覆盖背景、目标、关键动作、结果与复盘。",
                    "围绕低分维度持续补强，并定期验证改进效果。",
                ],
            ),
        ]
        return ReportLearningPlan(tags=tags, phases=phases)

    @staticmethod
    def _build_qa_analyses(transcript: list[TranscriptTurn]) -> list[ReportQaAnalysis]:
        analyses: list[ReportQaAnalysis] = []
        current_question = ""
        current_kind = "主问题"
        answer_parts: list[str] = []
        index = 0
        for turn in transcript:
            if turn.role == "interviewer" and turn.type in {
                "main_question",
                "follow_up",
                "question",
                "self_intro",
            }:
                if current_question:
                    index += 1
                    analyses.append(
                        ReportQaAnalysis(
                            question_index=index,
                            question=current_question,
                            answer="\n".join(answer_parts).strip(),
                            strengths=["有正面回应题目。"] if answer_parts else ["无"],
                            risks=(
                                ["本题未形成有效回答。"]
                                if not answer_parts
                                else ["回答仍可继续补充背景、动作与结果闭环。"]
                            ),
                            commentary=(
                                "候选人能够围绕问题作答，但仍需补充更完整的细节与判断依据。"
                                if answer_parts
                                else "本题未形成稳定回答，建议重点补强对应题型的表达模板。"
                            ),
                            approach=[
                                "优先按背景、目标、动作、结果的结构组织回答。",
                                "在追问场景下补足上一轮回答中缺失的关键细节。"
                                if current_kind == "追问"
                                else "增加更具体的数据、取舍和复盘内容。",
                            ],
                            reference_answer="可以结合一个最接近的真实案例，讲清场景、目标、关键动作、结果与复盘。",
                        )
                    )
                current_question = turn.text
                current_kind = "追问" if turn.type == "follow_up" else "主问题"
                answer_parts = []
                continue
            if current_question and turn.role == "candidate" and turn.type == "answer":
                answer_parts.append(turn.text)
        if current_question:
            index += 1
            analyses.append(
                ReportQaAnalysis(
                    question_index=index,
                    question=current_question,
                    answer="\n".join(answer_parts).strip(),
                    strengths=["有正面回应题目。"] if answer_parts else ["无"],
                    risks=(
                        ["本题未形成有效回答，态度比较敷衍。"]
                        if not answer_parts
                        else ["回答仍可继续补充背景、动作与结果闭环。"]
                    ),
                    commentary=(
                        "候选人能够围绕问题作答，但仍需补充更完整的细节与判断依据。"
                        if answer_parts
                        else "本题未形成稳定回答，态度比较敷衍，建议重点补强对应题型的表达模板。"
                    ),
                    approach=[
                        "优先按背景、目标、动作、结果的结构组织回答。",
                        "在追问场景下补足上一轮回答中缺失的关键细节。"
                        if current_kind == "追问"
                        else "增加更具体的数据、取舍和复盘内容。",
                    ],
                )
            )
        return analyses
