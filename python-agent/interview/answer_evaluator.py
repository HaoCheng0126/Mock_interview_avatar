from __future__ import annotations

import asyncio
import json
import logging

from interview.models import DimensionAssessment, Evaluation
from interview.prompts import DEFAULT_EVALUATOR_PROMPT, render_template

logger = logging.getLogger(__name__)


class AnswerEvaluator:
    def __init__(
        self,
        llm_client,
        *,
        prompt_template: str | None = None,
        context: dict | None = None,
    ) -> None:
        self._llm = llm_client
        self._prompt_template = prompt_template or DEFAULT_EVALUATOR_PROMPT
        self._context = dict(context or {})

    async def evaluate(
        self,
        question_text: str,
        answer_text: str,
        *,
        transcript: list[dict] | None = None,
        rubric_dimensions: list[str] | None = None,
    ) -> Evaluation:
        if transcript:
            transcript = list(transcript)[-8:]
        prompt = render_template(
            self._prompt_template,
            {
                **self._context,
                "question": question_text,
                "answer": answer_text,
                "rubric_dimensions": json.dumps(
                    rubric_dimensions or [], ensure_ascii=False
                ),
                "transcript": json.dumps(transcript or [], ensure_ascii=False),
            },
        )
        prompt += (
            "\n\n输出必须是单个 JSON 对象，字段固定为："
            '{"score":0,"dimensions":{},"strengths":[],"weaknesses":[],'
            '"followUpNeeded":false,"followUpQuestion":"",'
            '"dimensionAssessments":{}}。'
            "当回答明显过短、答非所问、只重复问题、只有态度词或没有说明任何动作/依据/结果时，"
            "必须将 followUpNeeded 设为 true；不能因为回答很短就直接放过。"
            "只有回答已经覆盖当前问题的关键事实时，才将 followUpNeeded 设为 false。"
            "followUpQuestion 必须结合当前问题和候选人刚才回答中的具体内容，一次只问一个问题；"
            "优先引用回答中的一个原词或短语，再追问它对应的具体动作、判断依据或结果。"
            "如果回答完全没有有效信息，就从原问题中选择一个最关键的缺口重新聚焦。"
            "禁止输出‘再具体讲讲’‘补充做法和结果’等脱离当前回答也成立的通用追问。"
        )
        try:
            data = await self._request_evaluation(prompt, timeout=3.6)
            evaluation = self._parse_evaluation(data)
            if self._is_low_information_answer(answer_text) and not self._has_usable_follow_up(
                evaluation
            ):
                repair_prompt = (
                    prompt
                    + "\n\n校验失败：候选人的回答属于低信息回答，但上一次结果没有给出可用追问。"
                    + "本次必须设置 followUpNeeded=true，并生成一个与当前回答和原问题直接相关的具体追问。"
                    + "如果回答中有可引用短语，必须在追问中引用；如果没有，就只聚焦原问题的一个关键缺口。"
                    + "\n上一次结果："
                    + json.dumps(data, ensure_ascii=False)
                )
                repaired = await self._request_evaluation(repair_prompt, timeout=2.2)
                repaired_evaluation = self._parse_evaluation(repaired)
                if self._has_usable_follow_up(repaired_evaluation):
                    evaluation = repaired_evaluation
            return evaluation
        except Exception as exc:
            logger.warning("answer evaluation fallback: %s", exc)
            return self._fallback(answer_text)

    async def _request_evaluation(self, prompt: str, *, timeout: float) -> dict:
        generate_json = getattr(self._llm, "generate_json_once", None)
        if callable(generate_json):
            raw = await asyncio.wait_for(
                generate_json(prompt, max_tokens=384, temperature=0.1),
                timeout=timeout,
            )
        else:
            raw = await asyncio.wait_for(
                self._llm.generate_once(prompt, max_tokens=384),
                timeout=timeout,
            )
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("answer evaluation must return a JSON object")
        return data

    @staticmethod
    def _parse_evaluation(data: dict) -> Evaluation:
        dimension_assessments = {}
        for key, value in (data.get("dimensionAssessments") or {}).items():
            if not isinstance(value, dict):
                continue
            dimension_assessments[str(key)] = DimensionAssessment(
                score=int(value.get("score") or 0),
                evidence=[str(item) for item in value.get("evidence") or []],
                concerns=[str(item) for item in value.get("concerns") or []],
                recommendations=[
                    str(item) for item in value.get("recommendations") or []
                ],
                confidence=str(value.get("confidence") or "low"),
            )
        return Evaluation(
            score=int(data.get("score") or 0),
            dimensions={
                str(key): int(value)
                for key, value in (data.get("dimensions") or {}).items()
            },
            strengths=[str(item) for item in data.get("strengths") or []],
            weaknesses=[str(item) for item in data.get("weaknesses") or []],
            follow_up_needed=bool(data.get("followUpNeeded")),
            follow_up_question=str(data.get("followUpQuestion") or "").strip(),
            dimension_assessments=dimension_assessments,
        )

    @staticmethod
    def _has_usable_follow_up(evaluation: Evaluation) -> bool:
        question = evaluation.follow_up_question.strip()
        return bool(evaluation.follow_up_needed and len(question) >= 6)

    @staticmethod
    def _is_low_information_answer(answer_text: str) -> bool:
        compact = "".join(str(answer_text or "").split())
        compact = compact.strip("，。！？、,.!?嗯呃哦啊哈呢吧了的")
        if len(compact) <= 12:
            return True
        evidence_markers = (
            "因为", "所以", "通过", "首先", "然后", "最后", "具体", "指标",
            "数据", "结果", "提升", "下降", "验证", "调研", "设计", "拆解",
            "上线", "迭代", "推进", "协调", "制定", "负责", "%",
        )
        marker_count = sum(1 for marker in evidence_markers if marker in compact)
        has_number = any(char.isdigit() for char in compact)
        return len(compact) <= 40 and marker_count + int(has_number) < 2

    @staticmethod
    def _fallback(answer_text: str) -> Evaluation:
        if len(answer_text.strip()) < 20:
            return Evaluation(
                score=2,
                dimensions={"clarity": 2},
                weaknesses=["回答较短，缺少细节"],
                follow_up_needed=True,
                follow_up_question="可以再具体讲讲你的做法和结果吗？",
            )
        return Evaluation(
            score=3,
            dimensions={"clarity": 3},
            strengths=["能够回应问题"],
            follow_up_needed=False,
        )
