"""Tests for interview.roster — avatar roster, selection policy, migration."""

import json

import pytest

from interview import roster as R


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect every roster path into a tmp config dir."""
    cfg = tmp_path / "config"
    avatars = cfg / "avatars"
    cfg.mkdir()
    monkeypatch.setattr(R, "CONFIG_DIR", cfg)
    monkeypatch.setattr(R, "ROSTER_PATH", cfg / "roster.json")
    monkeypatch.setattr(R, "AVATARS_DIR", avatars)
    monkeypatch.setattr(R, "HUB_SETTINGS_PATH", cfg / "hub_settings.json")
    monkeypatch.setattr(R, "LEGACY_INTERVIEW_YAML", cfg / "interview.yaml")
    return cfg


def test_safe_slug():
    assert R.safe_slug(" Tech Lead ") == "tech-lead"
    assert R.safe_slug("HR_面试官") == "hr"  # non-latin stripped
    assert R.safe_slug("") == "default"
    assert R.safe_slug("产品经理") == "default"  # all-non-latin → fallback


def test_config_path_default_vs_named(sandbox):
    default = R.AvatarEntry(slug="default")
    tech = R.AvatarEntry(slug="tech-lead")
    assert default.config_path() == R.LEGACY_INTERVIEW_YAML
    assert tech.config_path() == R.AVATARS_DIR / "tech-lead.yaml"


def test_normalize_fills_and_dedupes():
    roster = R._normalize(
        {
            "selection_mode": "bogus",
            "avatars": [
                {"slug": "a", "name": "A"},
                {"slug": "a", "name": "dup"},  # duplicate slug
                "not-a-dict",
            ],
        }
    )
    assert roster["selection_mode"] == "candidate_choice"  # bad mode reset
    slugs = [a["slug"] for a in roster["avatars"]]
    assert slugs == ["a", "a-2"]  # deduped
    assert roster["locked_avatar"] == "a"  # invalid/missing → first


def test_normalize_empty_yields_default():
    roster = R._normalize({})
    assert [a["slug"] for a in roster["avatars"]] == ["default"]


def test_resolve_candidate_choice_and_fallback():
    roster = R._normalize(
        {"selection_mode": "candidate_choice", "avatars": [{"slug": "a"}, {"slug": "b"}]}
    )
    assert R.resolve_avatar(roster, "b").slug == "b"
    assert R.resolve_avatar(roster, "missing").slug == "a"  # fallback to first
    assert R.resolve_avatar(roster, None).slug == "a"


def test_resolve_locked_ignores_request():
    roster = R._normalize(
        {
            "selection_mode": "locked",
            "locked_avatar": "b",
            "avatars": [{"slug": "a"}, {"slug": "b"}],
        }
    )
    assert R.resolve_avatar(roster, "a").slug == "b"  # request ignored while locked


def test_public_roster_hides_credentials():
    roster = R._normalize(
        {
            "avatars": [
                {
                    "slug": "a",
                    "name": "A",
                    "direction": "后端",
                    "avatar_id": "SECRET",
                    "poster_url": "https://example.com/a.jpg",
                }
            ]
        }
    )
    pub = R.public_roster(roster)
    assert pub["avatars"] == [
        {
            "slug": "a",
            "name": "A",
            "direction": "后端",
            "poster_url": "https://example.com/a.jpg",
        }
    ]
    assert "avatar_id" not in pub["avatars"][0]


def test_public_roster_exposes_safe_practice_defaults():
    roster = R._normalize(
        {
            "avatars": [
                {
                    "slug": "luffy",
                    "name": "路飞",
                    "default_role": "UI/UX设计师",
                    "default_jd": "岗位职责：负责游戏交互设计",
                    "profile_locked": True,
                }
            ]
        }
    )

    avatar = R.public_roster(roster)["avatars"][0]

    assert avatar["defaultRole"] == "UI/UX设计师"
    assert avatar["defaultJd"].startswith("岗位职责")
    assert avatar["profileLocked"] is True


def test_migration_seeds_default_from_existing_config(sandbox):
    (sandbox / "hub_settings.json").write_text(
        json.dumps(
            {"platform": {"avatar_id": "av_123", "voice_id": "vc_9", "voice_speed": "1.1"}}
        ),
        encoding="utf-8",
    )
    (sandbox / "interview.yaml").write_text(
        "interviewer:\n  name: 陈珊\ncandidate:\n  target_role: 产品经理\n", encoding="utf-8"
    )

    roster = R.load_roster()  # first load → seeds

    assert (sandbox / "roster.json").exists()
    assert len(roster["avatars"]) == 1
    default = R.entries(roster)[0]
    assert default.slug == "default"
    assert default.name == "陈珊"
    assert default.direction == "产品经理"
    assert default.avatar_id == "av_123"
    assert default.voice_id == "vc_9"
    # default avatar keeps editing the legacy interview.yaml — no file move
    assert default.config_path() == sandbox / "interview.yaml"


def test_save_roster_roundtrip(sandbox):
    saved = R.save_roster(
        {
            "selection_mode": "locked",
            "locked_avatar": "x",
            "avatars": [{"slug": "x", "name": "X"}],
        }
    )
    assert saved["selection_mode"] == "locked"
    reloaded = R.load_roster()
    assert reloaded["locked_avatar"] == "x"
    assert R.entries(reloaded)[0].name == "X"


def test_find_avatar(sandbox):
    roster = R._normalize({"avatars": [{"slug": "a"}, {"slug": "b"}]})
    assert R.find_avatar(roster, "b").slug == "b"
    assert R.find_avatar(roster, "missing") is None


def test_ensure_avatar_configs_seeds_new_from_default(sandbox):
    (sandbox / "interview.yaml").write_text("interviewer:\n  name: 默认\n", encoding="utf-8")
    roster = R._normalize({"avatars": [{"slug": "default"}, {"slug": "tech-lead"}]})
    R.ensure_avatar_configs(roster)
    assert not (sandbox / "avatars" / "default.yaml").exists()  # default uses shared file
    seeded = sandbox / "avatars" / "tech-lead.yaml"
    assert seeded.exists()
    assert "默认" in seeded.read_text(encoding="utf-8")  # copied from the default


def test_delete_avatar_removes_entry_and_file(sandbox):
    (sandbox / "interview.yaml").write_text("interviewer:\n  name: 默认\n", encoding="utf-8")
    roster = R.save_roster({"avatars": [{"slug": "default"}, {"slug": "tech-lead"}]})
    R.ensure_avatar_configs(roster)
    seeded = sandbox / "avatars" / "tech-lead.yaml"
    assert seeded.exists()
    after = R.delete_avatar(R.load_roster(), "tech-lead")
    assert [a["slug"] for a in after["avatars"]] == ["default"]
    assert not seeded.exists()


def test_delete_last_avatar_refused(sandbox):
    roster = R.save_roster({"avatars": [{"slug": "only"}]})
    with pytest.raises(ValueError, match="至少"):
        R.delete_avatar(roster, "only")


def test_delete_default_keeps_shared_interview_yaml(sandbox):
    iv = sandbox / "interview.yaml"
    iv.write_text("interviewer:\n  name: 默认\n", encoding="utf-8")
    roster = R.save_roster({"avatars": [{"slug": "default"}, {"slug": "b"}]})
    R.delete_avatar(roster, "default")
    assert iv.exists()  # the shared interview.yaml is never deleted
