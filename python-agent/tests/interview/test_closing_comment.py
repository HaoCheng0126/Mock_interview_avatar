"""Tests for interview.closing_comment — spoken end-of-interview recap (总评)."""

import asyncio

from interview.closing_comment import ClosingCommentGenerator
from interview.models import InterviewReport


class FakeLlm:
    def __init__(self, response):
        self._response = response
        self.calls = 0
        self.last_prompt = None
        self.prompt = ""

    async def generate(self, prompt, max_tokens=None):
        self.calls += 1
        self.last_prompt = prompt
        self.prompt = prompt
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    async def generate_once(self, prompt, max_tokens=None):
        self.calls += 1
        self.last_prompt = prompt
        self.prompt = prompt
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _report():
    return InterviewReport(
        summary="整体回答完整",
        overall_score=72,
        strengths=["项目讲得具体", "有量化结果"],
        weaknesses=["系统设计略浅"],
        recommendations=[],
        exchanges=[],
    )


def test_generates_spoken_recap_and_feeds_report_analysis():
    llm = FakeLlm("  整体来看你今天表现不错，项目讲得很具体，系统设计可以再深入一些。  ")
    text = asyncio.run(
        ClosingCommentGenerator(llm).generate_async(_report(), target_role="后端工程师")
    )
    assert text == "整体来看你今天表现不错，项目讲得很具体，系统设计可以再深入一些。"  # trimmed
    assert llm.calls == 1
    # the report's own analysis + the target role are fed into the recap prompt
    assert "项目讲得具体" in llm.last_prompt
    assert "系统设计略浅" in llm.last_prompt
    assert "后端工程师" in llm.last_prompt


def test_no_llm_returns_empty():
    # falls back to the plain closing line when no LLM is configured
    assert asyncio.run(ClosingCommentGenerator(None).generate_async(_report())) == ""


def test_no_report_returns_empty():
    llm = FakeLlm("won't be called")
    assert asyncio.run(ClosingCommentGenerator(llm).generate_async(None)) == ""
    assert llm.calls == 0


def test_llm_failure_returns_empty():
    llm = FakeLlm(RuntimeError("boom"))
    assert asyncio.run(ClosingCommentGenerator(llm).generate_async(_report())) == ""
