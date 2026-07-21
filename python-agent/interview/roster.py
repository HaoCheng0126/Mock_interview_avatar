"""Interview avatar roster — the per-avatar interview templates plus the policy
for who picks which one.

Each avatar carries its own interview config (persona, prompts, speech, question
bank, rubric). ``roster.json`` holds the avatar meta (display name, direction,
LiveAvatar ``avatar_id``/``voice_id``) and the selection policy: candidates
either choose from the roster, or are locked to a single avatar (e.g. a
dedicated-IP avatar that only runs one interview direction).

Global LLM / ASR / platform credentials stay in ``hub_settings.json`` — only the
avatar identity and interview content live here.

Migration: the first load seeds one ``default`` avatar from the existing
platform settings and interview.yaml, with NO file move — the default avatar's
config path stays ``config/interview.yaml`` so the current console keeps editing
it unchanged. New avatars get their own ``config/avatars/<slug>.yaml``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
ROSTER_PATH = CONFIG_DIR / "roster.json"
AVATARS_DIR = CONFIG_DIR / "avatars"
HUB_SETTINGS_PATH = CONFIG_DIR / "hub_settings.json"
LEGACY_INTERVIEW_YAML = Path(
    os.getenv("INTERVIEW_CONFIG_PATH", str(CONFIG_DIR / "interview.yaml"))
)

SELECTION_MODES = ("candidate_choice", "locked")
USAGE_TYPES = ("practice", "enterprise")
DEFAULT_SLUG = "default"
_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def safe_slug(value: str) -> str:
    """Normalize to a filesystem/URL-safe slug. Empty → the default slug."""
    slug = _SLUG_RE.sub("-", (value or "").strip().lower()).strip("-")
    return slug or DEFAULT_SLUG


@dataclass(frozen=True)
class AvatarEntry:
    slug: str
    name: str = ""
    direction: str = ""
    avatar_id: str = ""
    voice_id: str = ""
    voice_speed: str = ""
    poster_url: str = ""
    usage_type: str = "practice"
    default_role: str = ""
    default_jd: str = ""
    profile_locked: bool = False

    @classmethod
    def from_dict(cls, raw: dict) -> "AvatarEntry":
        return cls(
            slug=safe_slug(str(raw.get("slug") or "")),
            name=str(raw.get("name") or ""),
            direction=str(raw.get("direction") or ""),
            avatar_id=str(raw.get("avatar_id") or ""),
            voice_id=str(raw.get("voice_id") or ""),
            voice_speed=str(raw.get("voice_speed") or ""),
            poster_url=str(raw.get("poster_url") or ""),
            usage_type=(
                str(raw.get("usage_type") or "practice")
                if str(raw.get("usage_type") or "practice") in USAGE_TYPES
                else "practice"
            ),
            default_role=str(raw.get("default_role") or ""),
            default_jd=str(raw.get("default_jd") or ""),
            profile_locked=bool(raw.get("profile_locked", False)),
        )

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "direction": self.direction,
            "avatar_id": self.avatar_id,
            "voice_id": self.voice_id,
            "voice_speed": self.voice_speed,
            "poster_url": self.poster_url,
            "usage_type": self.usage_type,
            "default_role": self.default_role,
            "default_jd": self.default_jd,
            "profile_locked": self.profile_locked,
        }

    def public(self) -> dict:
        """Fields safe to expose to the candidate-facing prep page."""
        data = {"slug": self.slug, "name": self.name, "direction": self.direction}
        if self.poster_url:
            data["poster_url"] = self.poster_url
        if self.default_role:
            data["defaultRole"] = self.default_role
        if self.default_jd:
            data["defaultJd"] = self.default_jd
        if self.profile_locked:
            data["profileLocked"] = True
        return data

    def config_path(self) -> Path:
        """Where this avatar's interview.yaml lives. The default avatar keeps the
        legacy path so existing tooling edits it unchanged; others are namespaced."""
        if self.slug == DEFAULT_SLUG:
            return LEGACY_INTERVIEW_YAML
        return AVATARS_DIR / f"{self.slug}.yaml"


def load_roster() -> dict:
    """Return the normalized roster, seeding a default one on first run."""
    if not ROSTER_PATH.exists():
        _seed_default_roster()
    return _normalize(_read_json(ROSTER_PATH))


def save_roster(roster: dict) -> dict:
    normalized = _normalize(roster)
    AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    ROSTER_PATH.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return normalized


def entries(roster: dict) -> list[AvatarEntry]:
    return [AvatarEntry.from_dict(a) for a in roster["avatars"]]


def resolve_avatar(
    roster: dict,
    slug: str | None = None,
    *,
    usage_type: str | None = None,
) -> AvatarEntry:
    """Resolve which avatar to run. In ``locked`` mode the requested slug is
    ignored; otherwise the candidate's choice wins, falling back to the first."""
    all_entries = entries(roster)
    if usage_type in USAGE_TYPES:
        all_entries = [entry for entry in all_entries if entry.usage_type == usage_type]
    if not all_entries:
        raise ValueError("没有可用的该类型虚拟面试官")
    by_slug = {entry.slug: entry for entry in all_entries}
    if roster["selection_mode"] == "locked":
        return by_slug.get(roster["locked_avatar"], all_entries[0])
    if slug:
        chosen = by_slug.get(safe_slug(slug))
        if chosen is not None:
            return chosen
    return all_entries[0]


def public_roster(roster: dict) -> dict:
    """Roster shape for the prep page — no credentials, just what to display."""
    practice = [entry for entry in entries(roster) if entry.usage_type == "practice"]
    locked = roster["locked_avatar"]
    if locked not in {entry.slug for entry in practice}:
        locked = practice[0].slug if practice else ""
    return {
        "selection_mode": roster["selection_mode"],
        "locked_avatar": locked,
        "avatars": [entry.public() for entry in practice],
    }


def find_avatar(roster: dict, slug: str) -> AvatarEntry | None:
    target = safe_slug(slug)
    for entry in entries(roster):
        if entry.slug == target:
            return entry
    return None


def delete_avatar(roster: dict, slug: str) -> dict:
    """Remove an avatar and (for non-default avatars) its config file, then save.
    Refuses to remove the last avatar; never touches the shared interview.yaml."""
    target = safe_slug(slug)
    remaining = [a for a in roster["avatars"] if a["slug"] != target]
    if not remaining:
        raise ValueError("至少需要保留一个面试官")
    entry = find_avatar(roster, target)
    if entry is not None and entry.slug != DEFAULT_SLUG:
        try:
            entry.config_path().unlink()
        except FileNotFoundError:
            pass
    return save_roster({**roster, "avatars": remaining})


def ensure_avatar_configs(roster: dict) -> None:
    """Give every avatar an interview.yaml, seeding a new one from the default's
    config so it starts from a working template rather than an empty file."""
    AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    for entry in entries(roster):
        dest = entry.config_path()
        if dest == LEGACY_INTERVIEW_YAML or dest.exists():
            continue
        if LEGACY_INTERVIEW_YAML.exists():
            shutil.copyfile(LEGACY_INTERVIEW_YAML, dest)
        else:
            dest.write_text("", encoding="utf-8")


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _normalize(raw: dict) -> dict:
    avatars: list[dict] = []
    seen: set[str] = set()
    for item in raw.get("avatars") or []:
        if not isinstance(item, dict):
            continue
        entry = AvatarEntry.from_dict(item)
        slug = entry.slug
        while slug in seen:  # keep slugs unique
            slug = f"{slug}-2"
        avatars.append(
            AvatarEntry.from_dict({**entry.to_dict(), "slug": slug}).to_dict()
        )
        seen.add(slug)
    if not avatars:
        avatars = [AvatarEntry(slug=DEFAULT_SLUG, name="默认面试官").to_dict()]

    slugs = {a["slug"] for a in avatars}
    mode = raw.get("selection_mode")
    if mode not in SELECTION_MODES:
        mode = "candidate_choice"
    locked = raw.get("locked_avatar")
    if locked not in slugs:
        locked = avatars[0]["slug"]
    return {"selection_mode": mode, "locked_avatar": locked, "avatars": avatars}


def _seed_default_roster() -> None:
    AVATARS_DIR.mkdir(parents=True, exist_ok=True)
    platform = _read_json(HUB_SETTINGS_PATH).get("platform", {})
    doc = _read_yaml(LEGACY_INTERVIEW_YAML)
    name = str(
        (doc.get("interviewer") or {}).get("name")
        or platform.get("avatar_id")
        or "默认面试官"
    )
    direction = str((doc.get("candidate") or {}).get("target_role") or "")
    default = AvatarEntry(
        slug=DEFAULT_SLUG,
        name=name,
        direction=direction,
        avatar_id=str(platform.get("avatar_id") or ""),
        voice_id=str(platform.get("voice_id") or ""),
        voice_speed=str(platform.get("voice_speed") or ""),
        usage_type="practice",
    )
    save_roster(
        {
            "selection_mode": "candidate_choice",
            "locked_avatar": DEFAULT_SLUG,
            "avatars": [default.to_dict()],
        }
    )


def _read_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
