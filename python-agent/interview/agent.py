#!/usr/bin/env python3
"""Interview digital human agent."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import sys
import uuid
from pathlib import Path
from time import perf_counter

import aiohttp.web_protocol as aiohttp_web_protocol
import yaml
from aiohttp import web


# aiohttp 3.13.x on Python 3.14 can raise EINVAL while enabling TCP keepalive
# on an accepted localhost socket.  The exception is raised from
# RequestHandler.connection_made(), so the request then appears to hang even
# though the server process and port are both alive.  Keepalive is an optional
# transport optimization; ignoring only this socket-option failure restores
# normal request handling without weakening application-level timeouts.
_aiohttp_tcp_keepalive = aiohttp_web_protocol.tcp_keepalive


def _safe_aiohttp_tcp_keepalive(transport) -> None:
    try:
        _aiohttp_tcp_keepalive(transport)
    except OSError as exc:
        logging.getLogger(__name__).debug("TCP keepalive unavailable: %s", exc)


aiohttp_web_protocol.tcp_keepalive = _safe_aiohttp_tcp_keepalive

sys.path.insert(0, str(Path(__file__).parent.parent))

from liveavatar_channel_sdk import AvatarAgent, AvatarAgentConfig

from interview.answer_evaluator import AnswerEvaluator
from interview.controller import InterviewController
from interview.follow_up_decider import FollowUpDecider
from interview.interview_manager import InterviewManager
from interview.interview_planner import InterviewPlanner, InterviewPlanningError
from interview.listener import InterviewListener
from interview.models import InterviewState
from interview.prep_assets import (
    PREP_AUDIO_DIR,
    build_public_avatar_assets,
    render_prep_text,
    warm_roster_prep_assets,
)
from interview.profile import CandidateProfile, extract_resume_text
from interview.profile_analyzer import (
    CandidateBrief,
    analyze_candidate_profile,
)
from interview.question_planner import QuestionPlanner
from interview.closing_comment import ClosingCommentGenerator
from interview.report_generator import ReportGenerator
from interview.roster import entries, find_avatar, load_roster, resolve_avatar
from interview.enterprise_store import EnterpriseStore, build_recruiting_report
from interview.company_knowledge import prepare_company_knowledge
from interview.session_store import JsonInterviewStore
from llm_client import LlmClient
from hub.config_store import (
    effective_avatar_platform,
    load_settings as load_hub_settings,
)

HERE = Path(__file__).parent
FRONTEND = HERE.parent.parent / "frontend"

API_KEY = os.getenv("LIVEAVATAR_API_KEY", "")
AVATAR_ID = os.getenv("LIVEAVATAR_AVATAR_ID", "")
BASE_URL = os.getenv(
    "LIVEAVATAR_BASE_URL", "https://facemarket.ai/vih/dispatcher"
)
VOICE_ID = os.getenv("LIVEAVATAR_VOICE_ID", None)
SANDBOX = os.getenv("LIVEAVATAR_SANDBOX", "").strip().lower() in {"1", "true", "yes", "on"}
HTTP_PORT = int(os.getenv("INTERVIEW_HTTP_PORT", "8083"))
MAX_JD_SOURCE_CHARS = 30000
MAX_RESUME_SOURCE_CHARS = 60000
ASR_STARTUP_TIMEOUT = float(os.getenv("INTERVIEW_ASR_STARTUP_TIMEOUT", "12"))

ASR_PROVIDER = os.getenv("ASR_PROVIDER", "dashscope").strip().lower()
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
VOLC_ASR_APP_ID = os.getenv("VOLC_ASR_APP_ID", "")
VOLC_ASR_ACCESS_TOKEN = os.getenv("VOLC_ASR_ACCESS_TOKEN", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
REPORT_LLM_API_KEY = os.getenv("REPORT_LLM_API_KEY", DEEPSEEK_API_KEY)
REPORT_LLM_BASE_URL = os.getenv("REPORT_LLM_BASE_URL", DEEPSEEK_BASE_URL)
REPORT_LLM_MODEL = os.getenv("REPORT_LLM_MODEL", DEEPSEEK_MODEL)
REPORT_LLM_PROVIDER = os.getenv("REPORT_LLM_PROVIDER", "").strip()
REPORT_LLM_REQUESTED_PROVIDER = os.getenv(
    "REPORT_LLM_REQUESTED_PROVIDER", REPORT_LLM_PROVIDER
).strip()
REPORT_LLM_FALLBACK = os.getenv("REPORT_LLM_FALLBACK", "0") == "1"
GLOBAL_REPORT_PROMPT = os.getenv("INTERVIEW_GLOBAL_REPORT_PROMPT", "").strip()
GLOBAL_REPORT_OVERVIEW_PROMPT = os.getenv(
    "INTERVIEW_GLOBAL_REPORT_OVERVIEW_PROMPT", ""
).strip()
GLOBAL_REPORT_QA_PROMPT = os.getenv(
    "INTERVIEW_GLOBAL_REPORT_QA_PROMPT", ""
).strip()
ENTERPRISE_CONFIG_SNAPSHOT_DIR = Path(
    os.getenv("INTERVIEW_ENTERPRISE_CONFIG_DIR", "/tmp/liveavatar-enterprise-configs")
)

CONFIG_PATH = Path(
    os.getenv(
        "INTERVIEW_CONFIG_PATH",
        str(Path(__file__).parent.parent / "config" / "interview.yaml"),
    )
)
STORAGE_DIR = Path(os.getenv("INTERVIEW_STORAGE_DIR", "/tmp/liveavatar-interviews"))
ENTERPRISE_COOKIE = "enterprise_interview_session"
_enterprise_store = EnterpriseStore()

_agent: AvatarAgent | None = None
# Serializes session start so a second (e.g. preheat + click) request can't
# null out _agent mid-start — that race surfaced as "'NoneType' has no
# attribute 'start'", masking the real platform error.
_session_lock = asyncio.Lock()
_controller: InterviewController | None = None
_listener: InterviewListener | None = None
_asr_manager = None
_session_info: dict = {}
_direct_asr_sockets: set[web.WebSocketResponse] = set()
_last_interview_status: dict | None = None
_report_controllers: dict[str, InterviewController] = {}
_report_contexts: dict[str, dict] = {}
_latest_report_interview_id = ""
# The candidate's ephemeral JD/resume/role for the CURRENT session. Held only in
# memory, applied to the controller at interview start, and cleared when the
# interview ends — never written to disk (see interview.profile).
_candidate_profile = CandidateProfile()
# One bounded derivative of the source material. All downstream prompts use this.
_candidate_brief: CandidateBrief | None = None
_company_knowledge_context: tuple[str, str] = ("", "")
# One-shot guard for the prep-stage greeting. The candidate-facing prep page may
# reconnect/retry; do not repeat the greeting within the same session.
_prep_prompt_sent = False
# The interview.yaml path of the avatar chosen for the CURRENT session, so a
# controller rebuild at interview start loads the same avatar's config.
_session_config_path = CONFIG_PATH
_enterprise_record_id = ""
_last_status_enterprise = False
NO_STORE_HEADERS = {"Cache-Control": "no-store"}
logger = logging.getLogger(__name__)

ENTERPRISE_REPORT_PROMPT = """
你是企业招聘决策助手。请基于完整面试 Transcript、候选人结构化画像、岗位要求和公司背景，
生成仅供招聘方查看的 JSON 决策依据。不要写参考答案、学习计划、面试教程或鼓励性话术。
企业内部知识只能用于岗位匹配和待核验判断，不得因为候选人不知道未公开事实而直接扣分。
输出字段：
{
  "summary": "招聘结论",
  "overallScore": 0到100整数的岗位匹配分,
  "strengths": ["有候选人回答证据支持的优势"],
  "weaknesses": ["风险或待核验点"],
  "recommendations": ["建议复试问题"],
  "qaAnalyses": [
    {"questionIndex":1,"question":"问题","answer":"回答","strengths":[],"risks":[],
     "commentary":"招聘评价","approach":""}
  ],
  "learningPlan": {"tags": [], "phases": []}
}
完整面试记录：{transcript}
岗位要求/考察点：{core_competencies}
候选人画像：{candidate_brief}
"""


def json_response(data, **kwargs):
    return web.json_response(
        data, dumps=lambda obj: _json.dumps(obj, ensure_ascii=False), **kwargs
    )


def _current_avatar_name() -> str:
    avatar = _session_info.get("avatar") or {}
    return str(avatar.get("name") or "").strip()


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def build_controller(agent, config_path=None, question_plan=None) -> InterviewController:
    manager = InterviewManager(config_path or CONFIG_PATH)
    # Overlay only the compact analysis into prompts; the source text stays outside
    # every repeated LLM request.
    manager.apply_candidate_profile(_candidate_profile, _candidate_brief)
    manager.set_company_knowledge_context(*_company_knowledge_context)
    manager.set_runtime_context(avatar_name=_current_avatar_name())
    cfg = manager.config
    persona = manager.persona_context()
    evaluation_persona = manager.persona_context(include_internal=True)
    enterprise_evaluation_suffix = ""
    if _enterprise_record_id:
        enterprise_evaluation_suffix = (
            "\n公司可提问背景：{company_knowledge}\n"
            "企业内部评估依据：{company_internal_knowledge}\n"
            "内部依据不得出现在候选人可见内容中；不得因为候选人不知道未公开事实而直接扣分，"
            "只能用于岗位匹配与待核验判断。\n"
        )
    # question_plan (from the session-start planner) drives the questions; without it
    # (e.g. preheat before the profile is known) fall back to the static bank.
    questions = question_plan if question_plan is not None else manager.get_question_specs()
    llm_client = LlmClient(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        model=DEEPSEEK_MODEL,
        system_prompt=manager.build_system_prompt(),
    )
    report_llm_client = LlmClient(
        api_key=REPORT_LLM_API_KEY,
        base_url=REPORT_LLM_BASE_URL,
        model=REPORT_LLM_MODEL,
        system_prompt=manager.build_system_prompt(),
    )
    logger.info(
        "report LLM configured: requested=%s actual=%s fallback=%s model=%s",
        REPORT_LLM_REQUESTED_PROVIDER or "active",
        REPORT_LLM_PROVIDER or "active",
        REPORT_LLM_FALLBACK,
        REPORT_LLM_MODEL,
    )
    return InterviewController(
        agent=agent,
        manager=manager,
        planner=QuestionPlanner(questions),
        evaluator=AnswerEvaluator(
            llm_client,
            prompt_template=cfg.prompts.evaluator + enterprise_evaluation_suffix,
            context=evaluation_persona,
        ),
        report_generator=ReportGenerator(
            report_llm_client,
            require_ai_overview=True,
            prompt_template=(
                (
                    GLOBAL_REPORT_OVERVIEW_PROMPT
                    or GLOBAL_REPORT_PROMPT
                    or cfg.prompts.report
                )
                + (
                    "\n这是企业招聘决策报告：不要输出参考答案、学习教程或鼓励性话术；"
                    "优势、风险与结论必须有候选人回答证据。\n"
                    if _enterprise_record_id
                    else ""
                )
                + enterprise_evaluation_suffix
            ),
            qa_prompt_template=(
                (GLOBAL_REPORT_QA_PROMPT + enterprise_evaluation_suffix)
                if GLOBAL_REPORT_QA_PROMPT
                else None
            ),
            context=evaluation_persona,
        ),
        closing_comment_generator=(
            None
            if _enterprise_record_id
            else ClosingCommentGenerator(
                llm_client,
                prompt_template=cfg.prompts.closing_comment or None,
                context=persona,
            )
        ),
        follow_up_decider=FollowUpDecider(
            llm_client, prompt_template=cfg.prompts.follow_up_decider, context=persona
        ),
        session_store=JsonInterviewStore(STORAGE_DIR),
        interview_id=f"iv_{uuid.uuid4().hex[:8]}",
        # 思考中提醒：虚拟人真的说（前端不显示文案）
        thinking_checks=[
            (check.after_seconds, check.text)
            for check in cfg.speech.thinking_checks
        ],
        hard_timeout_seconds=cfg.workflow.hard_timeout_seconds,
        opening_to_question_delay_seconds=cfg.workflow.opening_to_question_delay_seconds,
        prompt_playback_timeout_seconds=cfg.workflow.prompt_playback_timeout_seconds,
        candidate_speech_grace_seconds=cfg.workflow.candidate_speech_grace_seconds,
        evaluation_join_timeout_seconds=cfg.workflow.evaluation_join_timeout_seconds,
        foreground_evaluation_timeout_seconds=(
            cfg.workflow.foreground_evaluation_timeout_seconds
        ),
        max_skipped_questions=cfg.workflow.max_skipped_questions,
        max_consecutive_skipped_questions=cfg.workflow.max_consecutive_skipped_questions,
        speech_config=cfg.speech,
        on_terminal=handle_interview_terminal,
    )


async def build_question_plan(config_path=None):
    """Build a question plan from the one-shot brief plus the matched static bank."""
    manager = InterviewManager(config_path or CONFIG_PATH)
    manager.apply_candidate_profile(_candidate_profile, _candidate_brief)
    manager.set_company_knowledge_context(*_company_knowledge_context)
    manager.set_runtime_context(avatar_name=_current_avatar_name())
    cfg = manager.config
    llm = None
    if DEEPSEEK_API_KEY:
        llm = LlmClient(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            model=DEEPSEEK_MODEL,
            system_prompt=manager.build_system_prompt(),
        )
    planner = InterviewPlanner(
        llm,
        prompt_template=cfg.prompts.planner or None,
        context=manager.persona_context(),
        self_intro_prompt=cfg.speech.self_intro_prompt or None,
        resume_experiences=cfg.plan.resume_experiences,
        business_questions=cfg.plan.business_questions,
        resume_followups=cfg.plan.resume_followups,
        business_followups=cfg.plan.business_followups,
        self_intro_followups=cfg.plan.self_intro_followups,
        self_intro_followups_no_resume=cfg.plan.self_intro_followups_no_resume,
        allow_fallback=not bool(_enterprise_record_id),
    )
    return await planner.build_plan(
        candidate_brief=(
            _candidate_brief.planner_context()
            if _candidate_brief is not None
            else manager.persona_context()["candidate_brief"]
        ),
        has_resume=bool(_candidate_profile.resume_text.strip()),
        target_role=_candidate_profile.target_role or cfg.candidate.target_role,
        bank=manager.matched_question_specs(),
        core_competencies=manager.persona_context()["core_competencies"],
        jd_text=_candidate_profile.jd_text,
        resume_text=_candidate_profile.resume_text,
    )


async def analyze_current_candidate() -> CandidateBrief:
    """Analyze the raw profile once; downstream components receive only the result."""
    client = None
    if DEEPSEEK_API_KEY:
        client = LlmClient(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            model=DEEPSEEK_MODEL,
            system_prompt=(
                "你负责把候选人原始资料压缩成忠实、可核验、适合面试出题的结构化画像。"
            ),
        )
    return await analyze_candidate_profile(_candidate_profile, client)


async def analyze_current_company_knowledge() -> tuple[str, str]:
    client = None
    if DEEPSEEK_API_KEY:
        client = LlmClient(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            model=DEEPSEEK_MODEL,
            system_prompt="你只负责忠实筛选和压缩企业面试知识，不得编造。",
        )
    return await prepare_company_knowledge(
        _session_config_path, _candidate_profile, client
    )


def _build_asr_manager(listener):
    """Pick the developer ASR by ASR_PROVIDER; None when creds are missing."""
    callbacks = dict(
        on_transcript=listener._on_asr_transcript,
        on_speech_started=listener._on_speech_started,
        on_speech_stopped=listener._on_speech_stopped,
        on_interim=listener._on_asr_interim,
    )
    if ASR_PROVIDER == "volcengine":
        if not (VOLC_ASR_APP_ID and VOLC_ASR_ACCESS_TOKEN):
            return None
        from interview.volcano_asr import VolcAsrManager

        return VolcAsrManager(**callbacks)
    if DASHSCOPE_API_KEY:
        from interview.asr_manager import QwenAsrManager

        return QwenAsrManager(**callbacks)
    return None


async def start_interview_session(
    avatar_slug: str | None = None,
    *,
    enterprise_record_id: str = "",
) -> tuple[str, str]:
    async with _session_lock:
        return await _do_start_interview_session(
            avatar_slug, enterprise_record_id=enterprise_record_id
        )


async def _do_start_interview_session(
    avatar_slug: str | None = None,
    *,
    enterprise_record_id: str = "",
) -> tuple[str, str]:
    global _agent, _controller, _listener, _asr_manager, _session_info
    global _last_interview_status, _session_config_path
    global _prep_prompt_sent, _enterprise_record_id, _last_status_enterprise
    global _candidate_profile, _candidate_brief, _company_knowledge_context

    logger = logging.getLogger(__name__)
    total_started_at = perf_counter()
    asr_connect_ms = 0
    avatar_start_ms = 0
    controller_build_ms = 0

    await stop_interview_session()
    _last_interview_status = None
    _prep_prompt_sent = False
    _enterprise_record_id = enterprise_record_id
    _last_status_enterprise = bool(enterprise_record_id)

    # Resolve which avatar (persona + interview config) this session runs.
    avatar = resolve_avatar(
        load_roster(),
        avatar_slug,
        usage_type="enterprise" if enterprise_record_id else "practice",
    )
    enterprise_record = (
        _enterprise_store.get(enterprise_record_id) if enterprise_record_id else None
    )
    if enterprise_record is not None:
        candidate = enterprise_record.get("candidate_snapshot") or {}
        _candidate_profile = CandidateProfile(
            target_role=str(enterprise_record.get("target_role") or "").strip(),
            jd_text=str(enterprise_record.get("jd_text") or "").strip()[
                :MAX_JD_SOURCE_CHARS
            ],
            resume_text=str(candidate.get("resume_text") or "").strip()[
                :MAX_RESUME_SOURCE_CHARS
            ],
        )
        _candidate_brief = None
        _company_knowledge_context = ("", "")
        snapshot = enterprise_record.get("interview_config_snapshot") or {}
        if snapshot:
            ENTERPRISE_CONFIG_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            snapshot_path = ENTERPRISE_CONFIG_SNAPSHOT_DIR / f"{enterprise_record_id}.yaml"
            snapshot_path.write_text(
                yaml.safe_dump(snapshot, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            _session_config_path = snapshot_path
        else:
            _session_config_path = avatar.config_path()
    else:
        _session_config_path = avatar.config_path()

    _listener = InterviewListener()
    _asr_manager = _build_asr_manager(_listener)
    if _asr_manager is not None:
        _listener.asr_manager = _asr_manager
    else:
        # WebSocket Agent 模式下 ASR 始终由开发者提供（见官方接入指南），
        # 平台侧没有 ASR 兜底 — 缺少识别凭证时语音作答不可用。
        logger.warning(
            "ASR 未配置（provider=%s）— 语音作答不可用，候选人仅能通过文本输入回答",
            ASR_PROVIDER,
        )

    # Per-avatar voice speed, falling back to the legacy env var for back-compat.
    voice_config = None
    voice_speed_raw = (avatar.voice_speed or os.getenv("LIVEAVATAR_VOICE_SPEED", "")).strip()
    if voice_speed_raw:
        try:
            voice_config = {"speed": float(voice_speed_raw)}
        except ValueError:
            logger.warning("语音语速不是数字，已忽略: %r", voice_speed_raw)

    # Platform credentials are global by default. An avatar can opt into an
    # independent API key/base URL in its edit page; resolve that choice for
    # every new session so saving an override does not require restarting 8083.
    platform_access: dict[str, object] = {
        "api_key": API_KEY,
        "base_url": BASE_URL,
        "sandbox": SANDBOX,
    }
    try:
        configured_access = effective_avatar_platform(
            load_hub_settings(), avatar.slug
        )
        if not configured_access["use_global"]:
            platform_access = {
                "api_key": configured_access["api_key"],
                "base_url": configured_access["base_url"],
                "sandbox": str(configured_access["sandbox"]).strip().lower()
                in {"1", "true", "yes", "on"},
            }
    except Exception as exc:
        logger.warning("读取面试官独立平台配置失败，继续使用全局配置: %s", exc)

    config = AvatarAgentConfig(
        api_key=str(platform_access["api_key"]),
        avatar_id=avatar.avatar_id or AVATAR_ID,
        base_url=str(platform_access["base_url"]),
        sandbox=bool(platform_access["sandbox"]),
        developer_asr=True,
        developer_tts=False,
        voice_id=avatar.voice_id or VOICE_ID,
        voice_config=voice_config,
        timeout=30.0,
    )
    _agent = AvatarAgent(config, _listener)
    _listener.agent = _agent

    async def _connect_asr() -> bool:
        nonlocal asr_connect_ms
        if _asr_manager is None:
            return False
        started_at = perf_counter()
        try:
            await asyncio.wait_for(
                _asr_manager.connect(), timeout=ASR_STARTUP_TIMEOUT
            )
        except Exception as exc:
            logger.warning(
                "ASR 启动失败或超时（%.1fs），数字人继续以文本作答模式启动: %s",
                ASR_STARTUP_TIMEOUT,
                exc,
            )
            return False
        else:
            asr_connect_ms = round((perf_counter() - started_at) * 1000)
            return True

    async def _start_avatar():
        nonlocal avatar_start_ms
        started_at = perf_counter()
        result = await _agent.start()
        avatar_start_ms = round((perf_counter() - started_at) * 1000)
        return result

    asr_task = asyncio.create_task(_connect_asr())
    avatar_task = asyncio.create_task(_start_avatar())
    try:
        asr_available, result = await asyncio.gather(asr_task, avatar_task)
    except Exception:
        await stop_interview_session()
        raise
    if not asr_available:
        _listener.asr_manager = None

    started_at = perf_counter()
    _controller = build_controller(_agent, _session_config_path)
    controller_build_ms = round((perf_counter() - started_at) * 1000)
    _listener.set_controller(_controller)

    _session_info = {
        "userToken": result.user_token,
        "sfuUrl": result.sfu_url,
        "sessionId": result.session_id,
        "asrAvailable": asr_available,
        "avatar_slug": avatar.slug,
        "avatar": avatar.public(),
        "enterprise": bool(enterprise_record_id),
    }
    logger.info(
        "startup timing: asr_connect_ms=%s avatar_start_ms=%s "
        "controller_build_ms=%s total_ms=%s",
        asr_connect_ms,
        avatar_start_ms,
        controller_build_ms,
        round((perf_counter() - total_started_at) * 1000),
    )
    return result.user_token, result.sfu_url


async def stop_interview_session() -> None:
    global _agent, _controller, _listener, _asr_manager, _session_info, _last_interview_status
    global _prep_prompt_sent, _enterprise_record_id
    _last_interview_status = None
    _prep_prompt_sent = False
    if _controller is not None and _controller_has_meaningful_interview(_controller):
        stopper = getattr(_controller, "stop", None)
        if stopper is not None:
            await stopper()
    await release_interview_session_resources()
    _enterprise_record_id = ""


def _controller_has_meaningful_interview(controller) -> bool:
    """Preheated or abandoned rooms must not generate reports or lock new rooms."""
    status_getter = getattr(controller, "get_status", None)
    if not callable(status_getter):
        return True
    try:
        transcript = status_getter().get("transcript") or []
    except Exception:
        return True
    return any(
        str(turn.get("type") or "")
        in {"main_question", "follow_up", "answer", "question_skipped"}
        for turn in transcript
        if isinstance(turn, dict)
    )


async def handle_interview_terminal(_state=None) -> None:
    await _finalize_terminal_session()


async def _finalize_terminal_session() -> None:
    global _last_interview_status, _candidate_profile, _candidate_brief
    global _enterprise_record_id, _last_status_enterprise, _company_knowledge_context
    if _controller is not None:
        status_getter = getattr(_controller, "get_status", None)
        if status_getter is not None:
            _last_interview_status = status_getter()
    if _enterprise_record_id and _last_interview_status is not None:
        _enterprise_store.complete(
            _enterprise_record_id,
            candidate_brief=(
                _candidate_brief.as_dict(include_sources=False)
                if _candidate_brief is not None
                else {}
            ),
            transcript=_last_interview_status.get("transcript") or [],
            report=build_recruiting_report(_last_interview_status),
            interview_id=str(_last_interview_status.get("interviewId") or ""),
        )
        _last_status_enterprise = True
    _candidate_profile = CandidateProfile()  # interview ended → drop candidate data
    _candidate_brief = None
    _company_knowledge_context = ("", "")
    await release_interview_session_resources()
    _enterprise_record_id = ""


def _remember_report_controller(
    controller: InterviewController,
    *,
    enterprise_record_id: str = "",
    candidate_brief_snapshot: dict | None = None,
) -> str:
    global _latest_report_interview_id
    status = controller.get_status()
    interview_id = str(status.get("interviewId") or "")
    if not interview_id:
        return ""
    _report_controllers[interview_id] = controller
    _report_contexts[interview_id] = {
        "enterprise_record_id": str(enterprise_record_id or ""),
        "candidate_brief": dict(candidate_brief_snapshot or {}),
    }
    _latest_report_interview_id = interview_id
    # Keep a small in-process history without allowing abandoned controllers to
    # grow unbounded during a long-running local Hub session.
    while len(_report_controllers) > 8:
        oldest_id = next(iter(_report_controllers))
        if oldest_id == _latest_report_interview_id:
            break
        _report_controllers.pop(oldest_id, None)
        _report_contexts.pop(oldest_id, None)
    return interview_id


def _get_report_controller(interview_id: str = "") -> InterviewController | None:
    target = str(interview_id or "").strip() or _latest_report_interview_id
    return _report_controllers.get(target)


async def _watch_report_completion(
    task: asyncio.Task,
    controller: InterviewController,
    *,
    interview_id: str = "",
) -> None:
    """Publish a background report result without tying it to the stop request."""
    global _last_interview_status, _last_status_enterprise
    try:
        await task
    except Exception:
        logger.exception("background report task failed")
    status = controller.get_status()
    _last_interview_status = status
    report_id = str(interview_id or status.get("interviewId") or "")
    context = _report_contexts.get(report_id, {})
    enterprise_record_id = str(context.get("enterprise_record_id") or "")
    if enterprise_record_id and status.get("finalReport") is not None:
        _enterprise_store.complete(
            enterprise_record_id,
            candidate_brief=dict(context.get("candidate_brief") or {}),
            transcript=status.get("transcript") or [],
            report=build_recruiting_report(status),
            interview_id=str(status.get("interviewId") or ""),
        )
        _last_status_enterprise = True


async def release_interview_session_resources() -> None:
    global _agent, _controller, _listener, _asr_manager, _session_info
    await release_realtime_session_resources()
    _controller = None


async def release_realtime_session_resources() -> None:
    """Release costly realtime media while preserving the report controller."""
    global _agent, _listener, _asr_manager, _session_info
    if _direct_asr_sockets:
        sockets = tuple(_direct_asr_sockets)
        _direct_asr_sockets.clear()
        for socket in sockets:
            try:
                await socket.close(code=1001, message=b"interview session closed")
            except Exception:
                pass
    if _asr_manager is not None:
        try:
            await _asr_manager.close()
        except Exception:
            pass
        _asr_manager = None
    if _agent is not None:
        try:
            await _agent.stop()
        except Exception:
            pass
        _agent = None
    _listener = None
    _session_info = {}


async def handle_index(request: web.Request) -> web.Response:
    response = web.FileResponse(FRONTEND / "interview.html", headers=NO_STORE_HEADERS)
    if getattr(request, "path", "/") == "/":
        response.del_cookie(ENTERPRISE_COOKIE, path="/")
    return response


async def _enterprise_record(request: web.Request) -> dict | None:
    cookies = getattr(request, "cookies", {}) if request is not None else {}
    return _enterprise_store.resolve_access(
        str(cookies.get(ENTERPRISE_COOKIE) or "")
    )


async def handle_enterprise_redeem(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    exchanged = _enterprise_store.exchange(str(body.get("token") or ""))
    if exchanged is None:
        return json_response(
            {"success": False, "error": "邀请无效、已兑换、已撤销或已过期"},
            status=403,
        )
    record, access_token = exchanged
    response = json_response({"success": True, "record": _safe_enterprise_context(record)})
    response.set_cookie(
        ENTERPRISE_COOKIE,
        access_token,
        httponly=True,
        samesite="Lax",
        max_age=30 * 24 * 60 * 60,
        path="/",
    )
    return response


def _safe_enterprise_context(record: dict) -> dict:
    candidate = record.get("candidate_snapshot") or {}
    return {
        "id": record["id"],
        "status": record["status"],
        "expires_at": record["expires_at"],
        "candidate_name": record.get("candidate_name", ""),
        "candidate_contact": record.get("candidate_contact", ""),
        "position_id": record.get("position_id", ""),
        "target_role": record.get("target_role", ""),
        "candidate_ready": bool(
            record.get("candidate_name") and str(candidate.get("resume_text") or "").strip()
        ),
        "avatar": record.get("avatar_snapshot") or {},
    }


async def handle_enterprise_context(request: web.Request) -> web.Response:
    record = await _enterprise_record(request)
    if record is None:
        return json_response({"success": False, "error": "企业面试会话无效"}, status=403)
    return json_response({"success": True, "record": _safe_enterprise_context(record)})


async def handle_interview_js(request: web.Request) -> web.Response:
    return web.FileResponse(FRONTEND / "interview.js", headers=NO_STORE_HEADERS)


async def handle_asr_worklet_js(request: web.Request) -> web.Response:
    return web.FileResponse(FRONTEND / "asr-worklet.js", headers=NO_STORE_HEADERS)


async def handle_report_debug(request: web.Request) -> web.Response:
    return web.FileResponse(FRONTEND / "report-debug.html", headers=NO_STORE_HEADERS)


async def handle_sdk_js(request: web.Request) -> web.Response:
    js_path = (
        FRONTEND
        / "node_modules"
        / "@sanseng"
        / "liveavatar-js-sdk"
        / "dist"
        / "index.full.umd.js"
    )
    if not js_path.exists():
        return web.Response(status=404)
    return web.Response(
        body=js_path.read_bytes(),
        content_type="application/javascript",
        headers=NO_STORE_HEADERS,
    )


async def handle_start_session(request: web.Request) -> web.Response:
    global _controller
    try:
        body = await request.json()
    except Exception:
        body = {}
    avatar_slug = (body or {}).get("avatar")
    enterprise_mode = bool((body or {}).get("enterprise"))
    enterprise_record = await _enterprise_record(request) if enterprise_mode else None
    if enterprise_mode and enterprise_record is None:
        return json_response({"success": False, "error": "企业邀请会话无效"}, status=403)
    if _controller is not None and _controller.state in {
        InterviewState.REPORT_GENERATING,
        InterviewState.REPORT_ERROR,
        InterviewState.COMPLETED,
        InterviewState.TERMINATED,
    }:
        # Compatibility for a report controller created before the realtime/report
        # split: detach it instead of blocking the next interview.
        report_id = _remember_report_controller(
            _controller,
            enterprise_record_id=_enterprise_record_id,
            candidate_brief_snapshot=(
                _candidate_brief.as_dict(include_sources=False)
                if _candidate_brief is not None
                else {}
            ),
        )
        report_task = getattr(_controller, "_report_task", None)
        if report_task is not None:
            asyncio.create_task(
                _watch_report_completion(
                    report_task,
                    _controller,
                    interview_id=report_id,
                )
            )
        await release_interview_session_resources()
    if _agent is not None:
        same_enterprise = bool(
            enterprise_record
            and _enterprise_record_id
            and enterprise_record["id"] == _enterprise_record_id
        )
        if same_enterprise:
            return json_response(
                {
                    "success": True,
                    "userToken": _session_info.get("userToken", ""),
                    "sfuUrl": _session_info.get("sfuUrl", ""),
                    "sessionId": _session_info.get("sessionId", ""),
                    "asrAvailable": _session_info.get("asrAvailable", False),
                    "directAsrAvailable": _session_info.get("asrAvailable", False),
                    "avatar": _session_info.get("avatar"),
                    "resumed": True,
                }
            )
        # A different page may arrive after the previous tab was closed before
        # the interview actually started. Browsers do not guarantee delivery of
        # unload beacons, so an idle/preheated room must be reclaimable here
        # instead of permanently locking every other avatar.
        if _controller is not None and _controller.state == InterviewState.IDLE:
            logger.info(
                "reclaiming idle interview room before starting a new session "
                "(old_session=%s, old_enterprise=%s)",
                _session_info.get("sessionId", ""),
                bool(_enterprise_record_id),
            )
            await stop_interview_session()
        if _agent is not None and (enterprise_record or _enterprise_record_id):
            return json_response(
                {"success": False, "error": "面试间正在使用，请稍后重试"},
                status=409,
            )
    try:
        if enterprise_record:
            avatar_slug = enterprise_record["avatar_slug"]
        user_token, sfu_url = await start_interview_session(
            avatar_slug,
            enterprise_record_id=(enterprise_record or {}).get("id", ""),
        )
        return json_response(
            {
                "success": True,
                "userToken": user_token,
                "sfuUrl": sfu_url,
                "sessionId": _session_info.get("sessionId", ""),
                "asrAvailable": _session_info.get("asrAvailable", False),
                "directAsrAvailable": _session_info.get("asrAvailable", False),
                "avatar": _session_info.get("avatar"),
            }
        )
    except Exception as exc:
        logging.getLogger(__name__).error("start-session failed: %s", exc)
        return json_response({"success": False, "error": str(exc)}, status=500)


async def handle_stop_session(request: web.Request) -> web.Response:
    global _candidate_profile, _candidate_brief, _company_knowledge_context
    query = getattr(request, "query", {}) if request is not None else {}
    requested_session_id = str(query.get("sessionId") or "")
    explicit_release = str(query.get("release") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    active_session_id = str(_session_info.get("sessionId") or "")
    if requested_session_id and active_session_id and requested_session_id != active_session_id:
        return json_response({"success": True, "staleSessionIgnored": True})
    if _enterprise_record_id and not explicit_release:
        # Page unload/network loss must not stop the globally active enterprise
        # interview unless the browser explicitly says the page is being closed;
        # the candidate can reconnect after a transient network interruption.
        return json_response({"success": True, "preserved": True})
    await stop_interview_session()
    # A practice browser teardown ends that ephemeral candidate session.
    _candidate_profile = CandidateProfile()
    _candidate_brief = None
    _company_knowledge_context = ("", "")
    return json_response({"success": True})


async def handle_session_info(request: web.Request) -> web.Response:
    return json_response(_session_info)


async def handle_interview_start(request: web.Request) -> web.Response:
    global _controller, _candidate_brief, _company_knowledge_context
    if _controller is None or _agent is None:
        return json_response({"success": False, "error": "No active session"}, status=400)
    if _controller.state == InterviewState.IDLE:
        # The session may have been preheated before the candidate saved their profile —
        # analyze its source material exactly once, then plan from the compact brief.
        if _candidate_brief is None:
            _candidate_brief = await analyze_current_candidate()
            _company_knowledge_context = await analyze_current_company_knowledge()
        try:
            question_plan = await build_question_plan(_session_config_path)
        except InterviewPlanningError as exc:
            return json_response(
                {
                    "success": False,
                    "retryable": True,
                    "error": str(exc),
                },
                status=503,
            )
        _controller = build_controller(
            _agent, _session_config_path, question_plan=question_plan
        )
        if _listener is not None:
            _listener.set_controller(_controller)
    if _enterprise_record_id:
        record = _enterprise_store.get(_enterprise_record_id) or {}
        _enterprise_store.mark_in_progress(
            _enterprise_record_id,
            candidate_name=str(record.get("candidate_name") or ""),
            candidate_contact=str(record.get("candidate_contact") or ""),
        )
    await _controller.start()
    # scene.ready fires once, typically during preheat before the interview
    # starts — replay it so the (possibly rebuilt) controller can open.
    if _listener is not None and getattr(_listener, "scene_ready_seen", False):
        await _controller.mark_scene_ready()
    return json_response({"success": True, "status": _controller.get_status()})


async def handle_interview_stop(request: web.Request) -> web.Response:
    global _controller, _last_interview_status, _candidate_profile, _candidate_brief
    global _enterprise_record_id, _last_status_enterprise, _company_knowledge_context
    if _controller is None:
        return json_response({"success": False, "error": "No active interview"}, status=409)
    try:
        body = await request.json()
    except Exception:
        body = {}
    requested_session_id = str((body or {}).get("sessionId") or "")
    active_session_id = str(_session_info.get("sessionId") or "")
    if requested_session_id and active_session_id and requested_session_id != active_session_id:
        return json_response({"success": True, "staleSessionIgnored": True})

    controller = _controller
    task = controller.begin_stop_report()
    status = controller.get_status()
    _last_interview_status = status
    candidate_brief_snapshot = (
        _candidate_brief.as_dict(include_sources=False)
        if _candidate_brief is not None
        else {}
    )
    interview_id = _remember_report_controller(
        controller,
        enterprise_record_id=_enterprise_record_id,
        candidate_brief_snapshot=candidate_brief_snapshot,
    )
    _controller = None
    if task is not None:
        asyncio.create_task(
            _watch_report_completion(
                task,
                controller,
                interview_id=interview_id,
            )
        )
    await release_realtime_session_resources()
    _candidate_profile = CandidateProfile()
    _candidate_brief = None
    _company_knowledge_context = ("", "")
    _enterprise_record_id = ""
    return json_response(
        {
            "success": True,
            "reportPending": True,
            "interviewId": status.get("interviewId", ""),
            "status": status,
        },
        status=202,
    )


async def handle_interview_report_retry(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    interview_id = str((body or {}).get("interviewId") or "")
    controller = _get_report_controller(interview_id)
    if controller is None:
        return json_response(
            {"success": False, "error": "没有可重试的报告任务"}, status=409
        )
    retry = getattr(controller, "retry_report_generation", None)
    if retry is None:
        return json_response(
            {"success": False, "error": "当前报告不可重试"}, status=409
        )
    ok = await retry()
    status = controller.get_status()
    if not ok:
        return json_response(
            {
                "success": False,
                "retryable": True,
                "error": "AI 综合结论仍未生成成功",
                "status": status,
            },
            status=503,
        )
    task = getattr(controller, "_report_task", None)
    if task is not None:
        report_id = str(status.get("interviewId") or interview_id)
        asyncio.create_task(
            _watch_report_completion(task, controller, interview_id=report_id)
        )
    return json_response(
        {
            "success": True,
            "reportPending": True,
            "interviewId": status.get("interviewId", ""),
            "status": status,
        },
        status=202,
    )


async def handle_interview_audio_input(request: web.Request) -> web.Response:
    if _listener is None:
        return json_response({"success": False, "error": "No active session"}, status=400)
    data = await request.json()
    enabled = bool(data.get("enabled"))
    exchange_id = str(data.get("exchangeId") or "")
    accepted = _listener.set_audio_input_enabled(enabled, exchange_id)
    if enabled and not accepted:
        return json_response(
            {"success": False, "error": "capture is not allowed for this exchange"},
            status=409,
        )
    return json_response(
        {"success": True, "enabled": enabled, "exchangeId": exchange_id}
    )


async def handle_browser_asr_answer(request: web.Request) -> web.Response:
    """Accept browser-ASR text only when bound to the currently open audio floor."""
    if _controller is None:
        return json_response({"success": False, "error": "No active interview"}, status=409)
    try:
        data = await request.json()
    except Exception:
        data = {}
    text = str(data.get("text") or "").strip()
    exchange_id = str(data.get("exchangeId") or "").strip()
    request_id = str(data.get("requestId") or f"browser_asr_{uuid.uuid4().hex[:8]}")
    status = _controller.get_status()
    current_exchange_id = str(
        (status.get("currentExchange") or {}).get("exchangeId") or ""
    )
    if (
        not text
        or not status.get("captureAllowed")
        or not exchange_id
        or exchange_id != current_exchange_id
    ):
        return json_response(
            {"success": False, "error": "stale or closed ASR exchange"}, status=409
        )
    await _controller.handle_answer(
        request_id, text, expected_exchange_id=exchange_id
    )
    return json_response({"success": True, "exchangeId": exchange_id})


async def handle_interview_asr_socket(request: web.Request) -> web.StreamResponse:
    """Low-latency browser PCM input and interim-caption return channel."""
    listener = _listener
    asr_manager = _asr_manager
    expected_session_id = str(_session_info.get("sessionId") or "")
    requested_session_id = str(request.query.get("sessionId") or "")
    if listener is None or asr_manager is None or not expected_session_id:
        return json_response(
            {"success": False, "error": "ASR session is not ready"}, status=409
        )
    if requested_session_id != expected_session_id:
        return json_response(
            {"success": False, "error": "ASR session mismatch"}, status=403
        )

    socket = web.WebSocketResponse(
        heartbeat=20.0,
        max_msg_size=64 * 1024,
        compress=False,
    )
    await socket.prepare(request)
    _direct_asr_sockets.add(socket)
    capture_active = False
    first_packet_at = 0.0
    capture_exchange_id = ""
    trailing_flush_task: asyncio.Task | None = None

    async def send_caption(payload: dict) -> None:
        if not socket.closed:
            await socket.send_json(payload, dumps=lambda item: _json.dumps(item, ensure_ascii=False))

    listener.add_caption_sink(send_caption)
    await socket.send_json(
        {
            "type": "ready",
            "sampleRate": 16000,
            "format": "pcm_s16le",
            "packetDurationMs": 20,
        }
    )
    logger.info("direct ASR browser socket connected")

    async def flush_trailing_silence() -> None:
        # When the user presses Stop there are no more microphone packets. Send
        # real-time silence so server VAD can still close the last utterance.
        silence_packet = bytes(320 * 2)  # 20 ms at 16 kHz, mono PCM16
        for _ in range(35):  # 700 ms: enough for explicit manual-final flush
            if socket.closed or listener is not _listener:
                break
            listener.feed_direct_audio(silence_packet)
            await asyncio.sleep(0.02)

    async def cancel_trailing_flush() -> None:
        nonlocal trailing_flush_task
        task = trailing_flush_task
        trailing_flush_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def flush_and_stop() -> None:
        try:
            await flush_trailing_silence()
        finally:
            listener.stop_direct_audio_input()

    try:
        async for message in socket:
            if message.type == web.WSMsgType.BINARY:
                packet = bytes(message.data)
                if not capture_active:
                    continue
                if not packet or len(packet) > 64 * 1024 or len(packet) % 2:
                    await socket.send_json(
                        {"type": "error", "message": "invalid PCM16 audio packet"}
                    )
                    continue
                if not first_packet_at:
                    first_packet_at = perf_counter()
                listener.feed_direct_audio(packet)
                continue

            if message.type != web.WSMsgType.TEXT:
                continue
            try:
                payload = _json.loads(message.data)
            except Exception:
                await socket.send_json(
                    {"type": "error", "message": "invalid control message"}
                )
                continue
            message_type = str(payload.get("type") or "")
            if message_type == "start":
                await cancel_trailing_flush()
                if int(payload.get("sampleRate") or 0) != 16000:
                    await socket.send_json(
                        {"type": "error", "message": "sampleRate must be 16000"}
                    )
                    continue
                exchange_id = str(payload.get("exchangeId") or "")
                if not listener.start_direct_audio_input(exchange_id):
                    await socket.send_json(
                        {
                            "type": "error",
                            "message": "capture is not allowed for this exchange",
                            "exchangeId": exchange_id,
                        }
                    )
                    continue
                capture_active = True
                capture_exchange_id = exchange_id
                first_packet_at = 0.0
                await socket.send_json(
                    {"type": "started", "exchangeId": exchange_id}
                )
            elif message_type == "stop":
                if capture_active:
                    capture_active = False
                    if bool(payload.get("flushFinal", False)):
                        await cancel_trailing_flush()
                        trailing_flush_task = asyncio.create_task(flush_and_stop())
                    else:
                        listener.stop_direct_audio_input()
                await socket.send_json(
                    {"type": "stopped", "exchangeId": capture_exchange_id}
                )
            elif message_type == "ping":
                await socket.send_json({"type": "pong"})
    except asyncio.TimeoutError:
        logger.info("direct ASR browser socket timed out")
    except ConnectionResetError:
        logger.info("direct ASR browser socket disconnected")
    finally:
        await cancel_trailing_flush()
        listener.remove_caption_sink(send_caption)
        if capture_active:
            listener.stop_direct_audio_input()
        _direct_asr_sockets.discard(socket)
        logger.info(
            "direct ASR browser socket closed%s",
            (
                f" after {round((perf_counter() - first_packet_at) * 1000)}ms audio"
                if first_packet_at
                else ""
            ),
        )
    return socket


async def handle_interview_prep_say(request: web.Request) -> web.Response:
    global _prep_prompt_sent
    if _agent is None:
        return json_response({"success": False, "error": "No active session"}, status=400)
    if _prep_prompt_sent:
        return json_response({"success": True, "skipped": True})
    text = render_prep_text(resolve_avatar(load_roster(), _session_info.get("avatar_slug")))
    if not text:
        return json_response({"success": True, "skipped": True})
    _prep_prompt_sent = True
    await _agent.send_prompt(text, metadata={"promptType": "prep"})
    return json_response({"success": True})


async def handle_roster(request: web.Request) -> web.Response:
    """Public roster for the prep page: selection policy + avatars to display."""
    roster = load_roster()
    practice_entries = [
        entry for entry in entries(roster) if entry.usage_type == "practice"
    ]
    locked_avatar = roster["locked_avatar"]
    if locked_avatar not in {entry.slug for entry in practice_entries}:
        locked_avatar = practice_entries[0].slug if practice_entries else ""
    return json_response(
        {
            "selection_mode": roster["selection_mode"],
            "locked_avatar": locked_avatar,
            "avatars": [
                build_public_avatar_assets(entry)
                for entry in practice_entries
            ],
        }
    )


async def _warm_prep_assets(_app: web.Application) -> None:
    try:
        await asyncio.to_thread(warm_roster_prep_assets, load_roster())
    except Exception:
        logger.exception("failed to warm prep assets")


async def handle_get_profile(request: web.Request) -> web.Response:
    return json_response(_candidate_profile.summary)


async def handle_post_profile(request: web.Request) -> web.Response:
    """Candidate profile intake.

    Practice candidates submit role/JD/resume. Enterprise candidates submit only
    identity; recruiter-bound role/JD/interview configuration come from the invite.
    Candidate source material stays in memory for this session only.
    """
    global _candidate_profile, _candidate_brief, _company_knowledge_context
    form = await request.post()
    target_role = str(form.get("target_role") or "")
    jd_text = str(form.get("jd_text") or "")
    resume_text = str(form.get("resume_text") or "")
    resume_file = form.get("resume_file")
    enterprise_mode = str(form.get("enterprise") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    enterprise_record = await _enterprise_record(request) if enterprise_mode else None
    candidate_name = str(form.get("candidate_name") or "").strip()
    candidate_contact = str(form.get("candidate_contact") or "").strip()
    if enterprise_record is not None:
        candidate_snapshot = enterprise_record.get("candidate_snapshot") or {}
        candidate_name = candidate_name or str(
            enterprise_record.get("candidate_name") or candidate_snapshot.get("name") or ""
        ).strip()
        candidate_contact = candidate_contact or str(
            enterprise_record.get("candidate_contact")
            or candidate_snapshot.get("contact")
            or ""
        ).strip()
        if not candidate_name:
            return json_response(
                {"success": False, "error": "企业后台尚未配置候选人姓名"}, status=400
            )
        target_role = str(enterprise_record.get("target_role") or "").strip()
        jd_text = str(enterprise_record.get("jd_text") or "").strip()
        if not target_role:
            return json_response(
                {"success": False, "error": "企业邀请未绑定岗位，请联系招聘方重新生成"},
                status=400,
            )
        # Enterprise candidates never upload a resume on this page. The immutable
        # recruiter snapshot is the sole resume source for planning and evaluation.
        resume_text = str(candidate_snapshot.get("resume_text") or "").strip()
        resume_file = None
    try:
        if resume_file is not None and getattr(resume_file, "file", None):
            data = resume_file.file.read()
            if data:
                # An uploaded file wins over pasted text.
                resume_text = extract_resume_text(resume_file.filename or "", data)
    except ValueError as exc:
        return json_response({"success": False, "error": str(exc)}, status=400)
    # Replace the entire form snapshot so clearing an optional JD really clears it.
    _candidate_profile = CandidateProfile(
        target_role=target_role.strip(),
        jd_text=jd_text.strip()[:MAX_JD_SOURCE_CHARS],
        resume_text=resume_text.strip()[:MAX_RESUME_SOURCE_CHARS],
    )
    # This is the only LLM call that can see the source JD/résumé.
    _candidate_brief = await analyze_current_candidate()
    _company_knowledge_context = await analyze_current_company_knowledge()
    if enterprise_record is not None:
        _enterprise_store.mark_in_progress(
            enterprise_record["id"],
            candidate_name=candidate_name,
            candidate_contact=candidate_contact,
        )
    return json_response(
        {
            "success": True,
            **_candidate_profile.summary,
        }
    )


async def handle_interview_status(request: web.Request) -> web.Response:
    query = getattr(request, "query", {}) if request is not None else {}
    requested_interview_id = str(query.get("interviewId") or "").strip()
    if requested_interview_id:
        report_controller = _get_report_controller(requested_interview_id)
        if report_controller is None:
            return json_response(
                {"state": InterviewState.IDLE.value, "interviewId": requested_interview_id},
                status=404,
            )
        report_status = report_controller.get_status()
        report_context = _report_contexts.get(requested_interview_id, {})
        if report_context.get("enterprise_record_id"):
            report_status["enterprise"] = True
            report_status["finalReport"] = None
        return json_response(report_status)

    enterprise_record = await _enterprise_record(request)
    if enterprise_record is not None and enterprise_record["status"] == "completed":
        return json_response(
            {
                "state": InterviewState.COMPLETED.value,
                "enterprise": True,
                "candidateMessage": "面试已完成，结果将由招聘方后续通知",
                "finalReport": None,
            }
        )
    if _controller is None:
        report_controller = _get_report_controller()
        if report_controller is not None:
            report_context = _report_contexts.get(
                _latest_report_interview_id, {}
            )
            if not report_context.get("enterprise_record_id"):
                return json_response(report_controller.get_status())
            if enterprise_record is not None:
                enterprise_status = report_controller.get_status()
                enterprise_status["enterprise"] = True
                enterprise_status["finalReport"] = None
                return json_response(enterprise_status)
        if _last_interview_status is not None:
            if _last_status_enterprise:
                return json_response(
                    {
                        "state": _last_interview_status.get("state"),
                        "enterprise": True,
                        "candidateMessage": "面试已完成，结果将由招聘方后续通知",
                        "finalReport": None,
                    }
                )
            return json_response(_last_interview_status)
        return json_response({"state": InterviewState.IDLE.value})
    status = _controller.get_status()
    latest_report = _get_report_controller()
    if latest_report is not None:
        latest_status = latest_report.get_status()
        latest_context = _report_contexts.get(_latest_report_interview_id, {})
        if (
            latest_status.get("interviewId") != status.get("interviewId")
            and not latest_context.get("enterprise_record_id")
        ):
            status["latestReportStatus"] = latest_status
    if _enterprise_record_id:
        status["enterprise"] = True
        status["finalReport"] = None
    return json_response(status)


async def create_app() -> web.Application:
    # client_max_size covers resume uploads (PDF/Word up to 10 MB).
    app = web.Application(client_max_size=10 * 1024 * 1024)
    PREP_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    app.router.add_get("/", handle_index)
    app.router.add_get("/enterprise", handle_index)
    app.router.add_get("/interview.js", handle_interview_js)
    app.router.add_get("/asr-worklet.js", handle_asr_worklet_js)
    app.router.add_get("/report-debug", handle_report_debug)
    app.router.add_get("/sdk.js", handle_sdk_js)
    app.router.add_static("/media/prep-audio", str(PREP_AUDIO_DIR))
    app.router.add_post("/api/start-session", handle_start_session)
    app.router.add_post("/api/stop-session", handle_stop_session)
    app.router.add_get("/api/session-info", handle_session_info)
    app.router.add_post("/api/interview/start", handle_interview_start)
    app.router.add_post("/api/interview/stop", handle_interview_stop)
    app.router.add_post("/api/interview/report/retry", handle_interview_report_retry)
    app.router.add_post("/api/interview/audio-input", handle_interview_audio_input)
    app.router.add_post("/api/interview/asr-answer", handle_browser_asr_answer)
    app.router.add_get("/ws/interview/asr", handle_interview_asr_socket)
    app.router.add_post("/api/interview/prep-say", handle_interview_prep_say)
    app.router.add_get("/api/interview/status", handle_interview_status)
    app.router.add_get("/api/roster", handle_roster)
    app.router.add_get("/api/interview/profile", handle_get_profile)
    app.router.add_post("/api/interview/profile", handle_post_profile)
    app.router.add_post("/api/enterprise/redeem", handle_enterprise_redeem)
    app.router.add_get("/api/enterprise/context", handle_enterprise_context)
    app.on_startup.append(_warm_prep_assets)
    return app


def main() -> None:
    setup_logging()
    web.run_app(create_app(), host="0.0.0.0", port=HTTP_PORT)


if __name__ == "__main__":
    main()
