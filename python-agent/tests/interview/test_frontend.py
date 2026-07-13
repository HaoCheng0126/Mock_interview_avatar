from pathlib import Path


FRONTEND_HTML = Path(__file__).parents[3] / "frontend" / "interview.html"


def test_interview_frontend_uses_interview_routes():
    html = FRONTEND_HTML.read_text(encoding="utf-8")

    assert "./sdk.js" in html
    assert "LivekitSDK.createClient" in html
    assert "/api/start-session" in html
    assert "/api/interview/status" in html
    assert "/api/interview/start" in html
    assert "/api/interview/audio-input" in html
    assert "ensureInterviewStarted" in html
    assert "currentQuestion" in html
    assert "finalReport" in html
    assert "candidateMessage" in html
    assert "progressPercent" in html
    assert "transcript" in html


def test_interview_frontend_exposes_sdk_connection_failures():
    html = FRONTEND_HTML.read_text(encoding="utf-8")

    assert "connection" in html
    assert "sdk:connected" in html
    assert "sdk:disconnected" in html
    assert "sdk:error" in html
    assert "connectWithTimeout" in html
    assert "SDK connect timeout" in html


def test_interview_frontend_uses_portrait_video_stage():
    html = FRONTEND_HTML.read_text(encoding="utf-8")

    assert 'id="avatar-stage"' in html
    assert "aspect-ratio: 9 / 16" in html
    assert 'document.getElementById("avatar-stage")' in html
    assert "video: { containerElement: container" in html
    assert 'id="avatar"' in html


def test_interview_frontend_has_audio_input_toggle():
    html = FRONTEND_HTML.read_text(encoding="utf-8")

    assert 'id="audio-input-toggle"' in html
    assert "toggleAudioInput" in html
    assert "stopAudioInput" in html
    assert "startAudioCapture" in html
    assert "stopAudioCapture" in html
    assert "} else if (!audioInputEnabled) {\n        await stopAudioInput();" in html
    assert "startMicrophone" not in html
    assert "stopMicrophone" in html
    assert "aria-pressed" in html
    assert "getUserMedia" in html
    assert "disableAudioInputTrack" in html
    assert "getMicrophoneTrack" in html
    assert "let audioInputEnabled = false;" in html
    assert "setBackendAudioInput" in html


def test_interview_frontend_uses_collapsible_transcript_drawer():
    html = FRONTEND_HTML.read_text(encoding="utf-8")
    interview_panel = html[html.index('<aside id="interview-panel"'):html.index("</aside>")]

    assert 'id="transcript-drawer"' in html
    assert 'aria-hidden="true"' in html
    assert 'id="transcript-toggle"' in html
    assert "setTranscriptOpen(false)" in html
    assert 'id="transcript"' not in interview_panel


def test_interview_frontend_does_not_report_audio_capture_as_start_failure():
    html = FRONTEND_HTML.read_text(encoding="utf-8")

    assert "startInterviewAfterConnect" in html
    assert "startAudioInputAfterConnect" in html
    assert "audio input failed: " in html
    assert "await applyAudioInputState();\n            await ensureInterviewStarted();" not in html


def test_interview_frontend_does_not_restart_audio_when_toggle_is_off():
    html = FRONTEND_HTML.read_text(encoding="utf-8")

    assert "await startAudioInputAfterConnect();" not in html
