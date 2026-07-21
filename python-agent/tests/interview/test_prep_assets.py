from interview import prep_assets as A
from interview.roster import AvatarEntry


def test_build_public_avatar_assets_includes_poster_and_prep_audio(monkeypatch):
    entry = AvatarEntry(
        slug="pm",
        name="陈珊",
        direction="产品经理",
        voice_id="voice_platform_1",
        poster_url="https://example.com/poster.jpg",
    )
    monkeypatch.setattr(A, "render_prep_text", lambda avatar: "请填写岗位信息")
    monkeypatch.setattr(
        A,
        "ensure_prep_audio",
        lambda avatar, text: "/media/prep-audio/pm__1234.wav",
    )

    payload = A.build_public_avatar_assets(entry)

    assert payload == {
        "slug": "pm",
        "name": "陈珊",
        "direction": "产品经理",
        "poster_url": "https://example.com/poster.jpg",
        "prepText": "请填写岗位信息",
        "posterUrl": "https://example.com/poster.jpg",
        "prepAudioUrl": "/media/prep-audio/pm__1234.wav",
    }


def test_prep_audio_path_changes_when_voice_id_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "PREP_AUDIO_DIR", tmp_path)
    first = AvatarEntry(slug="pm", voice_id="voice_a")
    second = AvatarEntry(slug="pm", voice_id="voice_b")

    path_a = A.prep_audio_path(first, "请填写岗位信息")
    path_b = A.prep_audio_path(second, "请填写岗位信息")

    assert path_a != path_b
    assert path_a.name.startswith("pm__")
    assert path_a.suffix == ".wav"
