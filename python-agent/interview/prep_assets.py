"""Prep-page static assets: poster image + cached prep audio.

The candidate prep page should switch instantly between interviewers without
starting a real LiveAvatar session. This module renders each avatar's prep text,
pre-generates a local audio file when possible, and exposes the public asset
payload consumed by the prep page.

Current provider strategy:
- `macos_say` on macOS by default, producing a cached WAV file.
- `none` on other platforms (the frontend falls back to browser speech).

The cache key already includes `voice_id`, so when a platform-specific offline
TTS provider becomes available we can swap the generator implementation without
changing the public contract or invalidation logic.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from interview.interview_manager import InterviewManager
from interview.prompts import render_template
from interview.roster import AvatarEntry, entries

logger = logging.getLogger("interview.prep_assets")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
PREP_AUDIO_DIR = CONFIG_DIR / "prep_audio"
PREP_AUDIO_ROUTE = "/media/prep-audio"
PREP_AUDIO_PROVIDER = (
    os.getenv("INTERVIEW_PREP_AUDIO_PROVIDER", "").strip().lower()
    or ("macos_say" if os.uname().sysname.lower() == "darwin" else "none")
)


def render_prep_text(entry: AvatarEntry) -> str:
    manager = InterviewManager(entry.config_path())
    manager.set_runtime_context(avatar_name=entry.name or entry.slug)
    return render_template(
        manager.config.speech.prep_template or "", manager.persona_context()
    ).strip()


def build_public_avatar_assets(entry: AvatarEntry, *, ensure_audio: bool = True) -> dict:
    prep_text = render_prep_text(entry)
    payload = entry.public()
    payload["prepText"] = prep_text
    if entry.poster_url:
        payload["posterUrl"] = entry.poster_url
    audio_url = None
    if ensure_audio:
        audio_url = ensure_prep_audio(entry, prep_text)
    else:
        audio_url = cached_prep_audio_url(entry, prep_text)
    if audio_url:
        payload["prepAudioUrl"] = audio_url
    return payload


def warm_roster_prep_assets(roster: dict, slugs: list[str] | None = None) -> None:
    selected = {str(slug).strip() for slug in (slugs or []) if str(slug).strip()}
    for entry in entries(roster):
        if selected and entry.slug not in selected:
            continue
        prep_text = render_prep_text(entry)
        ensure_prep_audio(entry, prep_text)


def cached_prep_audio_url(entry: AvatarEntry, prep_text: str) -> str | None:
    path = prep_audio_path(entry, prep_text)
    if path.exists():
        return f"{PREP_AUDIO_ROUTE}/{path.name}"
    return None


def ensure_prep_audio(entry: AvatarEntry, prep_text: str) -> str | None:
    if not prep_text:
        return None
    PREP_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    target = prep_audio_path(entry, prep_text)
    if not target.exists():
        _cleanup_stale_audio(entry, keep_name=target.name)
        if not _generate_audio(entry, prep_text, target):
            return None
    return f"{PREP_AUDIO_ROUTE}/{target.name}"


def prep_audio_path(entry: AvatarEntry, prep_text: str) -> Path:
    digest = hashlib.sha1(
        "\n".join(
            [
                entry.slug,
                entry.voice_id,
                entry.voice_speed,
                prep_text,
                PREP_AUDIO_PROVIDER,
            ]
        ).encode("utf-8")
    ).hexdigest()[:16]
    return PREP_AUDIO_DIR / f"{entry.slug}__{digest}.wav"


def _cleanup_stale_audio(entry: AvatarEntry, *, keep_name: str) -> None:
    for path in PREP_AUDIO_DIR.glob(f"{entry.slug}__*.wav"):
        if path.name == keep_name:
            continue
        try:
            path.unlink()
        except OSError:
            logger.debug("failed to remove stale prep audio: %s", path)


def _generate_audio(entry: AvatarEntry, prep_text: str, target: Path) -> bool:
    provider = PREP_AUDIO_PROVIDER
    if provider == "none":
        return False
    if provider == "macos_say":
        return _generate_with_macos_say(entry, prep_text, target)
    logger.warning("unknown prep audio provider: %s", provider)
    return False


def _generate_with_macos_say(entry: AvatarEntry, prep_text: str, target: Path) -> bool:
    say_bin = shutil.which("say")
    if not say_bin:
        logger.warning("say not found; prep audio cache skipped")
        return False
    ffmpeg_bin = shutil.which("ffmpeg")
    afconvert_bin = shutil.which("afconvert")
    if not ffmpeg_bin and not afconvert_bin:
        logger.warning("neither ffmpeg nor afconvert found; prep audio cache skipped")
        return False

    with tempfile.TemporaryDirectory(prefix="prep-audio-") as tmpdir:
        aiff = Path(tmpdir) / "prep.aiff"
        voice = _system_voice_name(entry.voice_id)
        if not _run_say(say_bin, prep_text, aiff, voice=voice):
            if voice and not _run_say(say_bin, prep_text, aiff, voice=None):
                return False
        ok = _convert_to_wav(aiff, target, ffmpeg_bin=ffmpeg_bin, afconvert_bin=afconvert_bin)
        if ok:
            logger.info("generated prep audio cache for %s -> %s", entry.slug, target.name)
        return ok


def _run_say(say_bin: str, text: str, out: Path, *, voice: str | None) -> bool:
    cmd = [say_bin]
    if voice:
        cmd.extend(["-v", voice])
    cmd.extend(["-o", str(out), text])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0 and out.exists():
        return True
    logger.warning("say failed (%s): %s", proc.returncode, (proc.stderr or proc.stdout).strip())
    return False


def _convert_to_wav(
    source: Path,
    target: Path,
    *,
    ffmpeg_bin: str | None,
    afconvert_bin: str | None,
) -> bool:
    if ffmpeg_bin:
        proc = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-ac",
                "1",
                "-ar",
                "22050",
                str(target),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and target.exists():
            return True
        logger.warning("ffmpeg convert failed: %s", (proc.stderr or proc.stdout).strip())
    if afconvert_bin:
        proc = subprocess.run(
            [
                afconvert_bin,
                "-f",
                "WAVE",
                "-d",
                "LEI16@22050",
                str(source),
                str(target),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and target.exists():
            return True
        logger.warning("afconvert convert failed: %s", (proc.stderr or proc.stdout).strip())
    return False


def _system_voice_name(voice_id: str) -> str | None:
    raw = str(voice_id or "").strip()
    if not raw:
        return None
    if raw.startswith(("voice_", "avatar_", "vc_")):
        return None
    return raw
