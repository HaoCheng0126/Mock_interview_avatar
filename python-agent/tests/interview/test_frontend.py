"""Static anchors for the candidate-facing interview page (html + js).

New layout: avatar video stays on the left; the right #side panel switches
between the prep form (#pane-prep) and the live conversation (#pane-interview);
the report is full-screen (#view-finished).
"""

from pathlib import Path


FRONTEND = Path(__file__).parents[3] / "frontend"
FRONTEND_HTML = FRONTEND / "interview.html"
FRONTEND_JS = FRONTEND / "interview.js"
ENTERPRISE_HUB_HTML = FRONTEND / "hub-enterprise.html"
ENTERPRISE_HUB_JS = FRONTEND / "hub-enterprise.js"
HUB_HTML = FRONTEND / "hub.html"
HUB_INTERVIEW_HTML = FRONTEND / "hub-interview.html"
HUB_INTERVIEW_JS = FRONTEND / "hub-interview.js"


def _html() -> str:
    return FRONTEND_HTML.read_text(encoding="utf-8")


def _js() -> str:
    return FRONTEND_JS.read_text(encoding="utf-8")


def test_layout_has_persistent_avatar_and_switching_side_panel():
    html = _html()
    assert 'id="avatar-side"' in html          # avatar column, always present
    assert 'id="avatar-stage"' in html
    assert "aspect-ratio: 9 / 16" in html
    for pane_id in ("pane-prep", "pane-interview", "view-finished"):
        assert f'id="{pane_id}"' in html, pane_id
    assert 'data-view="prep"' in html          # landing view is prep
    assert "./sdk.js" in html
    assert "/interview.js" in html
    # dev-console + old separate-view leftovers must be gone
    for legacy in ('id="connect"', 'id="disconnect"', "transcript-drawer", "view-connecting"):
        assert legacy not in html, legacy


def test_prep_pane_keeps_profile_intake_fields():
    html = _html()
    for field_id in (
        "prep-role",
        "prep-jd",
        "prep-resume-file",
        "prep-resume-text",
    ):
        assert f'id="{field_id}"' in html, field_id
    assert 'id="start-btn"' in html
    assert 'id="readiness"' in html
    assert 'id="prep-error"' in html            # connection failure surfaces here
    assert 'id="start-hint"' in html            # gate hint when required fields are empty
    assert 'id="start-btn" disabled' in html    # start disabled until role+resume filled
    assert "系统不会自动生成或补全" in html
    assert "generate-jd-btn" not in html


def test_stale_report_lock_is_not_reported_as_backend_connection_failure():
    js = _js()
    assert 'text.includes("上一场面试报告尚未生成完成")' in js
    assert "上一场报告仍待处理" in js


def test_report_model_can_be_configured_and_tested_in_report_card():
    html = HUB_HTML.read_text(encoding="utf-8")
    for field_id in (
        "report-llm-provider",
        "report-llm-api_key",
        "report-llm-base_url",
        "report-llm-model",
        "report-llm-test-btn",
        "report-llm-save-btn",
    ):
        assert f'id="{field_id}"' in html
    assert "function saveReportLlm()" in html
    assert 'runTest("report-llm", "/api/config/test-llm"' in html


def test_each_interviewer_can_inherit_or_override_liveavatar_platform_access():
    html = HUB_INTERVIEW_HTML.read_text(encoding="utf-8")
    js = HUB_INTERVIEW_JS.read_text(encoding="utf-8")
    for field_id in (
        "platform-use-global",
        "platform-use-custom",
        "platform-global-summary",
        "platform-custom-fields",
        "platform-custom-api-key",
        "platform-custom-base-url",
        "platform-custom-sandbox",
    ):
        assert f'id="{field_id}"' in html
    assert "使用全局平台配置" in html
    assert "使用独立平台配置" in html
    assert "/api/interview-platform" in js
    assert "function collectAvatarPlatform()" in js
    assert "await saveAvatarPlatform()" in js
    assert 'avatarPlatformUrl("/test")' in js


def test_interviewer_editor_supports_multiple_non_repeating_transitions():
    html = HUB_INTERVIEW_HTML.read_text(encoding="utf-8")
    js = HUB_INTERVIEW_JS.read_text(encoding="utf-8")
    assert 'id="s-next_question_transitions"' in html
    assert "下一题转场（每行一条）" in html
    assert '"next_question_transitions"' in js
    assert '"next_question_transition"' not in js
    assert '(cfg.speech?.[key] ?? []).join("\\n")' in js


def test_interviewer_editor_exposes_follow_up_wait_budget():
    html = HUB_INTERVIEW_HTML.read_text(encoding="utf-8")
    js = HUB_INTERVIEW_JS.read_text(encoding="utf-8")
    assert 'id="w-foreground_evaluation_timeout_seconds"' in html
    assert '"foreground_evaluation_timeout_seconds"' in js


def test_direct_asr_pipeline_is_prepared_once_and_pcm_remains_state_gated():
    js = _js()
    assert "async function prepareDirectAsrPipeline()" in js
    assert "S.directAsrPrepared" in js
    assert "track.enabled = false" in js
    assert "track.enabled = true" in js
    assert "captureAllowedFor(S.lastStatus, exchangeId)" in js
    assert 'JSON.stringify({ type: "stop", flushFinal })' in js
    assert "setInterval(refreshStatus, 180)" in js


def test_report_generation_uses_real_progress_and_retry_ui():
    html = _html()
    js = _js()
    for element_id in (
        "report-progress-fill",
        "report-progress-percent",
        "report-progress-detail",
        "report-generation-error",
        "report-retry-btn",
        "report-skip-btn",
        "prep-report-entry",
        "report-ai-source",
    ):
        assert f'id="{element_id}"' in html
    assert "status.reportGeneration" in js
    assert "/api/interview/report/retry" in js
    assert '"llm_partial"' in js
    assert "本次综合结论不是由 AI 生成" in js


def test_report_can_continue_in_background_without_exposing_retry_counts():
    html = _html()
    js = _js()
    assert "稍后查看" in html
    assert "继续生成" in html
    assert "报告生成中" in html
    assert "查看面试报告" in js
    assert "function dismissReportToPrep()" in js
    assert "function openReportFromPrep()" in js
    assert 'if (!S.reportDismissed && !TERMINAL_STATES.has(status.state))' in js
    assert 'setView("prep")' in js
    assert "AI 重试" not in js
    assert "attempt}/${maxAttempts" not in js
    assert 'reportInterviewId: localStorage.getItem("lastReportInterviewId")' in js
    assert 'interviewId: S.reportInterviewId' in js
    assert "reportLocked" not in js
    assert "/api/interview/status?interviewId=" in js
    assert "staleSessionIgnored" not in js
    assert "sessionId=${encodeURIComponent(sessionToClose)}" in js
    assert "&release=1" in js
    assert "if (event.persisted) return" in js


def test_report_loading_is_a_mutually_exclusive_full_page_state():
    html = _html()
    js = _js()
    assert "min-height: 100dvh" in html
    assert "#view-finished [hidden] { display: none !important; }" in html
    assert 'id="report" hidden' in html
    assert "report.hidden = true" in js
    assert "loading.hidden = true" in js
    for stage in ("preprocessing", "chunk_analysis", "overview", "validating"):
        assert f'data-report-stage="{stage}"' in html


def test_enterprise_candidate_uses_invitation_position_instead_of_editing_job():
    html = _html()
    js = _js()
    assert 'id="candidate-job-fields"' in html
    assert 'id="candidate-resume-fields"' in html
    assert 'id="enterprise-position-name"' in html
    assert '$("candidate-job-fields").hidden = true' in js
    assert '$("candidate-resume-fields").hidden = true' in js
    assert '$("check-resume").hidden = true' in js
    assert '$("enterprise-position-name").textContent = context.record.target_role' in js
    assert 'form.append("candidate_name"' in js
    assert 'form.append("candidate_contact"' in js
    enterprise_identity_branch = js.split("if (ENTERPRISE_MODE)", 1)[1].split("} else {", 1)[0]
    assert 'form.append("resume_text"' not in enterprise_identity_branch


def test_enterprise_admin_selects_position_when_creating_invite():
    html = ENTERPRISE_HUB_HTML.read_text(encoding="utf-8")
    js = ENTERPRISE_HUB_JS.read_text(encoding="utf-8")
    assert 'id="position"' in html
    assert 'id="position-jd"' in html
    assert "/api/enterprise/positions" in js
    assert "position_id" in js
    assert "renderPositionPreview" in js
    for field_id in (
        "position-form",
        "position-title",
        "position-description",
        "position-save",
        "positions",
    ):
        assert f'id="{field_id}"' in html
    assert "savePosition" in js
    assert "editPosition" in js
    assert "deletePosition" in js


def test_enterprise_admin_manages_candidates_before_creating_tasks():
    html = ENTERPRISE_HUB_HTML.read_text(encoding="utf-8")
    js = ENTERPRISE_HUB_JS.read_text(encoding="utf-8")
    for field_id in (
        "candidate-form",
        "candidate-name",
        "candidate-contact",
        "candidate-resume-file",
        "candidate-resume-text",
        "candidate",
        "candidates",
    ):
        assert f'id="{field_id}"' in html
    assert "/api/enterprise/candidates" in js
    assert "candidate_id" in js
    assert "createCandidate" in js
    assert "recordLinkCell" in js
    assert "renewInviteLink" in js
    assert "showReport" in js


def test_enterprise_candidate_identity_is_read_only_and_resume_is_hidden():
    html = _html()
    js = _js()
    assert 'id="prep-candidate-name" readonly' in html
    assert 'id="prep-candidate-contact" readonly' in html
    assert '$("candidate-resume-fields").hidden = true' in js
    assert "candidate_ready" in js


def test_practice_profile_explicitly_disables_enterprise_mode():
    js = _js()
    assert 'form.append("enterprise", "true")' in js
    assert 'form.append("enterprise", "false")' in js


def test_avatar_can_prefill_practice_role_and_jd_without_overwriting_manual_edits():
    js = _js()
    assert "function applyAvatarDefaults(avatar)" in js
    assert "avatar.defaultRole" in js
    assert "avatar.defaultJd" in js
    assert "roleInput.value === previous.role" in js
    assert "jdInput.value === previous.jd" in js
    assert "applyAvatarDefaults(getAvatarBySlug(slug))" in js


def test_locked_avatar_forces_read_only_role_and_jd():
    html = _html()
    js = _js()
    assert 'id="prep-role-tip"' in html
    assert 'id="prep-jd-tip"' in html
    assert "avatar.profileLocked" in js
    assert "roleInput.readOnly = locked" in js
    assert "jdInput.readOnly = locked" in js
    assert "const replaceRole = locked ||" in js
    assert "const replaceJd = locked ||" in js
    assert "岗位 JD 已由该面试官预设，本场不可修改" in js


def test_interview_pane_composer_anchors():
    html = _html()
    assert 'id="chat-log"' in html
    assert 'id="progress-fill"' in html
    assert 'id="state-pill"' in html
    assert 'id="end-btn"' in html
    for anchor in ("composer-bar", "mic-btn", "wave-bars", "wave-stop", "answer-input", "send-btn"):
        assert f'id="{anchor}"' in html, anchor


def test_js_preheats_dedupes_and_cleans_up():
    js = _js()
    assert "preheat()" in js
    assert "/api/start-session" in js
    assert "_startInflight" in js               # concurrent start-session de-dupe
    assert "sendBeacon" in js
    assert "IDLE_RELEASE_MS" in js
    assert "/api/interview/start" in js
    assert "/api/interview/status" in js
    assert "updateStartGate" in js              # start button gated on required prep fields
    assert "/api/interview/generate-jd" not in js
    assert "onGenerateJd" not in js
    assert "正在分析岗位与简历" in js


def test_js_supports_voice_and_text_answers():
    js = _js()
    assert "sendTextQuestion" in js
    assert "startDirectAsrCapture" in js
    assert "AudioWorkletNode" in js
    assert "/ws/interview/asr" in js
    assert "/asr-worklet.js" in js
    assert "startAudioCapture" in js
    assert "stopAudioCapture" in js
    assert "conversation:asr:chunk" in js
    assert "getMicrophoneAudioLevel" in js
    assert "asrAvailable" in js
    assert "getMicrophoneTrack" in js           # meter the SDK's published track (real level)
    assert "stopMicrophone" not in js


def test_voice_capture_only_runs_after_interviewer_finishes_speaking():
    js = _js()
    assert 'const LISTENING_STATES = new Set(["listening"])' in js
    assert '"正在聆听你的回答"' in js
    assert '"面试官说话中"' in js
    assert "syncMicWithInterviewState(status)" in js
    assert "status?.captureAllowed === true" in js
    assert "currentExchangeId(status) === exchangeId" in js
    assert 'socket.send(JSON.stringify({ type: "start", sampleRate: 16000, exchangeId }))' in js
    assert '"/api/interview/asr-answer"' in js
    assert "S.voiceReplyEnabled" in js
    assert "S.client.interrupt" not in js
    assert "bargeIn(" not in js
    assert "可开口打断" not in js
    assert "S.voiceReplyEnabled = !S.micOn" not in js


def test_voice_transcript_keeps_one_unbounded_growing_bubble():
    html = _html()
    js = _js()
    assert "voiceCaptionCommitted" in js
    assert "appendCaptionSegment" in js
    assert 'updateVoiceCaption(message.text, message.type === "final")' in js
    assert 'let draft = $("draft-bubble")' in js
    assert "S.collectedTranscript = appendCaptionSegment" in js
    # Browser ASR may restart internally, but a restart must not clear the answer.
    onstart = js.split("S.speechRecognition.onstart", 1)[1].split("};", 1)[0]
    assert 'S.collectedTranscript = ""' not in onstart
    assert "max-height: none" in html
    assert "overflow: visible" in html


def test_js_maps_platform_errors_by_name():
    js = _js()
    assert "mapPlatformError" in js
    # SESSION_START_FAILED (avatar not published) maps to a friendly message
    for name in ("SESSION_START_FAILED", "PRINCIPAL_UNIDENTIFIED"):
        assert name in js, name
    assert "debug" in js
    assert "FIXTURE_STATUS" in js
    assert "finalReport" in js
    assert "candidateMessage" in js
