from pathlib import Path


def test_broadcast_page_has_manual_reply_input():
    html_path = Path(__file__).parents[3] / "frontend" / "broadcast.html"
    html = html_path.read_text(encoding="utf-8")

    assert 'id="manual-message"' in html
    assert 'id="btn-send-message"' in html
    assert "sendManualMessage()" in html
    assert "fetch('/api/comment'" in html
