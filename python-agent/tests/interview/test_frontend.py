"""Static anchors for the candidate-facing interview page (html + js)."""

from pathlib import Path


FRONTEND = Path(__file__).parents[3] / "frontend"
FRONTEND_HTML = FRONTEND / "interview.html"
FRONTEND_JS = FRONTEND / "interview.js"


def _html() -> str:
    return FRONTEND_HTML.read_text(encoding="utf-8")


def _js() -> str:
    return FRONTEND_JS.read_text(encoding="utf-8")


def test_page_has_four_views_and_loads_external_script():
    html = _html()
    for view_id in ("view-prep", "view-connecting", "view-interview", "view-finished"):
        assert f'id="{view_id}"' in html, view_id
    assert 'data-view="prep"' in html  # landing view is prep
    assert "./sdk.js" in html
    assert "/interview.js" in html
    # dev-console leftovers must not exist on the candidate page
    for legacy in ('id="connect"', 'id="disconnect"', "transcript-drawer"):
        assert legacy not in html, legacy


def test_prep_view_keeps_profile_intake_fields():
    html = _html()
    for field_id in ("prep-role", "prep-jd", "prep-resume-file", "prep-resume-text"):
        assert f'id="{field_id}"' in html, field_id
    assert 'id="start-btn"' in html
    assert 'id="readiness"' in html


def test_interview_view_layout_anchors():
    html = _html()
    assert 'id="avatar-stage"' in html
    assert "aspect-ratio: 9 / 16" in html
    assert 'id="chat-log"' in html
    assert 'id="progress-fill"' in html
    assert 'id="state-pill"' in html
    assert 'id="end-btn"' in html
    # composer supports both input modes
    for anchor in ("mode-voice", "mode-text", "mic-btn", "answer-input", "send-btn"):
        assert f'id="{anchor}"' in html, anchor


def test_js_preheats_session_and_cleans_up():
    js = _js()
    assert "preheat()" in js
    assert "/api/start-session" in js
    assert "sendBeacon" in js
    assert "IDLE_RELEASE_MS" in js
    assert "/api/interview/start" in js
    assert "/api/interview/status" in js


def test_js_supports_voice_and_text_answers():
    js = _js()
    assert "sendTextQuestion" in js
    assert "startAudioCapture" in js
    assert "stopAudioCapture" in js
    assert "conversation:asr:chunk" in js
    assert "getMicrophoneAudioLevel" in js
    assert "asrAvailable" in js
    # dead private-API calls from the old page must stay gone
    assert "getMicrophoneTrack" not in js
    assert "stopMicrophone" not in js


def test_js_maps_platform_errors_and_debug_mode():
    js = _js()
    assert "mapPlatformError" in js
    for code in ("40003", "40004", "40006"):
        assert code in js, code
    assert "debug" in js
    assert "FIXTURE_STATUS" in js
    assert "finalReport" in js
    assert "candidateMessage" in js
