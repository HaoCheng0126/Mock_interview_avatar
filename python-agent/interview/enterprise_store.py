"""Persistent enterprise candidates, interview tasks, and recruiting records.

Recruiters own candidate profiles and resume intake.  Every interview stores
immutable candidate/position/interview snapshots so later edits to the master
records cannot change a task that has already been issued.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "enterprise_interviews.db"
)
RETENTION_DAYS = 180

ENTERPRISE_POSITIONS: tuple[dict[str, str], ...] = (
    {
        "id": "digital-human-ai-product",
        "title": "数字人AI产品",
        "jd": """岗位职责
1、负责AI智能对话相关产品业务流程分析、智能对话相关产品方向规划；
2、负责落地AI虚拟数字人产品C端商业化应用场景，需求分析、方案设计；
3、跟踪产品数据，评估效果，推动产品功能优化及效果提升；
4、负责产品团队的管理工作，推动产品效果优化及产品方案落地。

任职要求
1、全日制统招本科以上学历；
2、产品相关工作经验，有海外商业化经验。对于AI技术前沿有敏锐的洞察力；有AI大模型业务背景优先考虑；
3、具备产品宏观视角，具备良好的产品规划能力、良好的数据分析和挖掘能力；
4、拥有成熟的产品设计能力，熟练使用Sketch、Axure等产品原型设计工具，同时具备PC端、移动端产品设计经验者优先；
5、较强的逻辑思维能力，以终为始；能够指引团队探索未知、克服各种限制，达成既定目标。""",
    },
)


def list_enterprise_positions() -> list[dict[str, str]]:
    return [dict(item) for item in ENTERPRISE_POSITIONS]


def get_enterprise_position(position_id: str) -> dict[str, str] | None:
    target = str(position_id or "").strip()
    for item in ENTERPRISE_POSITIONS:
        if item["id"] == target:
            return dict(item)
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class EnterpriseStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(
            path or os.getenv("INTERVIEW_ENTERPRISE_DB") or DEFAULT_DB_PATH
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS enterprise_candidates (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    contact TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT '',
                    resume_filename TEXT NOT NULL DEFAULT '',
                    resume_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS enterprise_positions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    jd TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            existing_positions = con.execute(
                "SELECT COUNT(*) FROM enterprise_positions"
            ).fetchone()[0]
            if existing_positions == 0:
                now = _iso()
                con.executemany(
                    """
                    INSERT INTO enterprise_positions
                    (id, title, jd, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (item["id"], item["title"], item["jd"], now, now)
                        for item in ENTERPRISE_POSITIONS
                    ],
                )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS enterprise_interviews (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    invite_token TEXT NOT NULL DEFAULT '',
                    avatar_slug TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    revoked_at TEXT,
                    candidate_name TEXT NOT NULL DEFAULT '',
                    candidate_contact TEXT NOT NULL DEFAULT '',
                    candidate_id TEXT NOT NULL DEFAULT '',
                    position_id TEXT NOT NULL DEFAULT '',
                    target_role TEXT NOT NULL DEFAULT '',
                    jd_text TEXT NOT NULL DEFAULT '',
                    avatar_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    candidate_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    position_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    interview_config_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    candidate_brief_json TEXT NOT NULL DEFAULT '{}',
                    transcript_json TEXT NOT NULL DEFAULT '[]',
                    report_json TEXT NOT NULL DEFAULT '{}',
                    interview_id TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {
                row[1] for row in con.execute("PRAGMA table_info(enterprise_interviews)")
            }
            if "access_hash" not in columns:
                con.execute(
                    "ALTER TABLE enterprise_interviews ADD COLUMN access_hash TEXT"
                )
            if "redeemed_at" not in columns:
                con.execute(
                    "ALTER TABLE enterprise_interviews ADD COLUMN redeemed_at TEXT"
                )
            if "invite_token" not in columns:
                con.execute(
                    "ALTER TABLE enterprise_interviews "
                    "ADD COLUMN invite_token TEXT NOT NULL DEFAULT ''"
                )
            if "position_id" not in columns:
                con.execute(
                    "ALTER TABLE enterprise_interviews "
                    "ADD COLUMN position_id TEXT NOT NULL DEFAULT ''"
                )
            if "jd_text" not in columns:
                con.execute(
                    "ALTER TABLE enterprise_interviews "
                    "ADD COLUMN jd_text TEXT NOT NULL DEFAULT ''"
                )
            for column in (
                "candidate_id",
                "candidate_snapshot_json",
                "position_snapshot_json",
                "interview_config_snapshot_json",
            ):
                if column in columns:
                    continue
                column_type = "TEXT NOT NULL DEFAULT '{}'" if column.endswith("_json") else "TEXT NOT NULL DEFAULT ''"
                con.execute(
                    f"ALTER TABLE enterprise_interviews ADD COLUMN {column} {column_type}"
                )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_enterprise_status "
                "ON enterprise_interviews(status, created_at DESC)"
            )

    def create_candidate(
        self,
        *,
        name: str,
        contact: str = "",
        source: str = "",
        resume_filename: str = "",
        resume_text: str = "",
    ) -> dict:
        clean_name = str(name or "").strip()
        clean_resume = str(resume_text or "").strip()[:20000]
        if not clean_name:
            raise ValueError("请填写候选人姓名")
        if not clean_resume:
            raise ValueError("请上传或粘贴候选人简历")
        candidate_id = f"cand_{secrets.token_hex(8)}"
        now = _iso()
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO enterprise_candidates
                (id, name, contact, source, resume_filename, resume_text,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    clean_name,
                    str(contact or "").strip(),
                    str(source or "").strip(),
                    str(resume_filename or "").strip(),
                    clean_resume,
                    now,
                    now,
                ),
            )
        return self.get_candidate(candidate_id)

    def create_position(self, *, title: str, jd: str) -> dict:
        clean_title = str(title or "").strip()
        clean_jd = str(jd or "").strip()[:30000]
        if not clean_title:
            raise ValueError("请填写岗位名称")
        if not clean_jd:
            raise ValueError("请填写岗位 JD")
        position_id = f"pos_{secrets.token_hex(8)}"
        now = _iso()
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO enterprise_positions
                (id, title, jd, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (position_id, clean_title, clean_jd, now, now),
            )
        return self.get_position(position_id)

    def update_position(self, position_id: str, *, title: str, jd: str) -> dict | None:
        clean_title = str(title or "").strip()
        clean_jd = str(jd or "").strip()[:30000]
        if not clean_title:
            raise ValueError("请填写岗位名称")
        if not clean_jd:
            raise ValueError("请填写岗位 JD")
        with self._connect() as con:
            cursor = con.execute(
                """
                UPDATE enterprise_positions
                SET title=?, jd=?, updated_at=? WHERE id=?
                """,
                (clean_title, clean_jd, _iso(), position_id),
            )
        return self.get_position(position_id) if cursor.rowcount else None

    def get_position(self, position_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM enterprise_positions WHERE id=?", (position_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_positions(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM enterprise_positions ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_position(self, position_id: str) -> bool:
        with self._connect() as con:
            cursor = con.execute(
                "DELETE FROM enterprise_positions WHERE id=?", (position_id,)
            )
        return cursor.rowcount > 0

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row | None, *, include_resume: bool) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        item["resume_chars"] = len(item.get("resume_text") or "")
        if not include_resume:
            item.pop("resume_text", None)
        return item

    def get_candidate(self, candidate_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM enterprise_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
        return self._candidate_from_row(row, include_resume=True)

    def list_candidates(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM enterprise_candidates ORDER BY created_at DESC"
            ).fetchall()
        return [self._candidate_from_row(row, include_resume=False) for row in rows]

    def delete_candidate(self, candidate_id: str) -> bool:
        with self._connect() as con:
            cursor = con.execute(
                "DELETE FROM enterprise_candidates WHERE id=?", (candidate_id,)
            )
        return cursor.rowcount > 0

    def create_invite(
        self,
        avatar_slug: str,
        expires_days: int,
        avatar_snapshot: dict,
        *,
        candidate: dict,
        position_id: str = "",
        target_role: str = "",
        jd_text: str = "",
        position_snapshot: dict | None = None,
        interview_config_snapshot: dict | None = None,
    ) -> tuple[dict, str]:
        if not str(candidate.get("id") or "").strip():
            raise ValueError("请选择候选人")
        if not str(candidate.get("name") or "").strip():
            raise ValueError("候选人缺少姓名")
        if not str(candidate.get("resume_text") or "").strip():
            raise ValueError("候选人缺少简历")
        days = min(30, max(1, int(expires_days or 7)))
        token = secrets.token_urlsafe(32)
        record_id = f"ent_{secrets.token_hex(8)}"
        created = _now()
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO enterprise_interviews
                (id, token_hash, invite_token, avatar_slug, created_at, expires_at,
                 candidate_name, candidate_contact, candidate_id,
                 position_id, target_role, jd_text, avatar_snapshot_json,
                 candidate_snapshot_json, position_snapshot_json,
                 interview_config_snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    _token_hash(token),
                    token,
                    avatar_slug,
                    _iso(created),
                    _iso(created + timedelta(days=days)),
                    str(candidate.get("name") or "").strip(),
                    str(candidate.get("contact") or "").strip(),
                    str(candidate.get("id") or "").strip(),
                    str(position_id or "").strip(),
                    str(target_role or "").strip(),
                    str(jd_text or "").strip(),
                    json.dumps(avatar_snapshot, ensure_ascii=False),
                    json.dumps(candidate, ensure_ascii=False),
                    json.dumps(position_snapshot or {}, ensure_ascii=False),
                    json.dumps(interview_config_snapshot or {}, ensure_ascii=False),
                ),
            )
        return self.get(record_id), token

    def renew_invite(self, record_id: str) -> tuple[dict, str] | None:
        token = secrets.token_urlsafe(32)
        with self._connect() as con:
            cursor = con.execute(
                """
                UPDATE enterprise_interviews
                SET token_hash=?, invite_token=?, access_hash=NULL, redeemed_at=NULL
                WHERE id=? AND status='pending'
                """,
                (_token_hash(token), token, record_id),
            )
        if not cursor.rowcount:
            return None
        return self.get(record_id), token

    def _normalized(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        if item["status"] in {"pending", "in_progress"}:
            try:
                expired = datetime.fromisoformat(item["expires_at"]) <= _now()
            except ValueError:
                expired = True
            if expired:
                item["status"] = "expired"
                with self._connect() as con:
                    con.execute(
                        "UPDATE enterprise_interviews SET status='expired' WHERE id=?",
                        (item["id"],),
                    )
        for field in (
            "avatar_snapshot_json",
            "candidate_snapshot_json",
            "position_snapshot_json",
            "interview_config_snapshot_json",
            "candidate_brief_json",
            "transcript_json",
            "report_json",
        ):
            public_name = field.removesuffix("_json")
            try:
                item[public_name] = json.loads(item.pop(field) or "{}")
            except json.JSONDecodeError:
                item[public_name] = {} if field != "transcript_json" else []
        item.pop("token_hash", None)
        item.pop("access_hash", None)
        return item

    def get(self, record_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM enterprise_interviews WHERE id=?", (record_id,)
            ).fetchone()
        return self._normalized(row)

    def exchange(self, token: str) -> tuple[dict, str] | None:
        if not token:
            return None
        access_token = secrets.token_urlsafe(32)
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM enterprise_interviews WHERE token_hash=?",
                (_token_hash(token),),
            ).fetchone()
            record = self._normalized(row)
            if (
                not record
                or record["status"] not in {"pending", "in_progress"}
                or record.get("redeemed_at")
            ):
                return None
            con.execute(
                """
                UPDATE enterprise_interviews
                SET access_hash=?, redeemed_at=? WHERE id=? AND redeemed_at IS NULL
                """,
                (_token_hash(access_token), _iso(), record["id"]),
            )
        return self.get(record["id"]), access_token

    def resolve_access(self, access_token: str) -> dict | None:
        if not access_token:
            return None
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM enterprise_interviews WHERE access_hash=?",
                (_token_hash(access_token),),
            ).fetchone()
        record = self._normalized(row)
        if not record or record["status"] not in {
            "pending",
            "in_progress",
            "completed",
        }:
            return None
        return record

    def list(self) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT * FROM enterprise_interviews ORDER BY created_at DESC"
            ).fetchall()
        return [self._normalized(row) for row in rows]

    def mark_in_progress(
        self,
        record_id: str,
        *,
        candidate_name: str,
        candidate_contact: str,
        target_role: str = "",
    ) -> dict:
        with self._connect() as con:
            con.execute(
                """
                UPDATE enterprise_interviews
                SET status='in_progress',
                    started_at=COALESCE(started_at, ?),
                    candidate_name=?, candidate_contact=?,
                    target_role=CASE WHEN target_role='' THEN ? ELSE target_role END
                WHERE id=? AND status IN ('pending', 'in_progress')
                """,
                (
                    _iso(),
                    candidate_name,
                    candidate_contact,
                    target_role,
                    record_id,
                ),
            )
        return self.get(record_id)

    def complete(
        self,
        record_id: str,
        *,
        candidate_brief: dict,
        transcript: list,
        report: dict,
        interview_id: str,
    ) -> dict:
        with self._connect() as con:
            con.execute(
                """
                UPDATE enterprise_interviews
                SET status='completed', completed_at=?, candidate_brief_json=?,
                    transcript_json=?, report_json=?, interview_id=?
                WHERE id=?
                """,
                (
                    _iso(),
                    json.dumps(candidate_brief, ensure_ascii=False),
                    json.dumps(transcript, ensure_ascii=False),
                    json.dumps(report, ensure_ascii=False),
                    interview_id,
                    record_id,
                ),
            )
        return self.get(record_id)

    def revoke(self, record_id: str) -> dict | None:
        with self._connect() as con:
            con.execute(
                """
                UPDATE enterprise_interviews
                SET status='revoked', revoked_at=?
                WHERE id=? AND status IN ('pending', 'in_progress')
                """,
                (_iso(), record_id),
            )
        return self.get(record_id)

    def delete(self, record_id: str) -> bool:
        with self._connect() as con:
            cursor = con.execute(
                "DELETE FROM enterprise_interviews WHERE id=?", (record_id,)
            )
        return cursor.rowcount > 0

    def avatar_has_records(self, avatar_slug: str) -> bool:
        with self._connect() as con:
            row = con.execute(
                "SELECT 1 FROM enterprise_interviews WHERE avatar_slug=? LIMIT 1",
                (avatar_slug,),
            ).fetchone()
        return row is not None

    def cleanup(self, retention_days: int = RETENTION_DAYS) -> int:
        cutoff = _iso(_now() - timedelta(days=max(1, retention_days)))
        with self._connect() as con:
            con.execute(
                """
                UPDATE enterprise_interviews SET status='expired'
                WHERE status IN ('pending', 'in_progress') AND expires_at <= ?
                """,
                (_iso(),),
            )
            cursor = con.execute(
                """
                DELETE FROM enterprise_interviews
                WHERE COALESCE(completed_at, revoked_at, expires_at, created_at) < ?
                """,
                (cutoff,),
            )
        return cursor.rowcount


def build_recruiting_report(status: dict) -> dict[str, Any]:
    """Map evaluated evidence to the enterprise-only decision report schema."""
    source = status.get("finalReport") or {}
    score = max(0, min(100, int(source.get("overallScore") or 0)))
    if score >= 85:
        recommendation = "强烈建议录用"
    elif score >= 70:
        recommendation = "建议录用"
    elif score >= 55:
        recommendation = "待定"
    else:
        recommendation = "不建议录用"
    qa = []
    for item in source.get("qaAnalyses") or []:
        qa.append(
            {
                "question": item.get("question", ""),
                "answer": item.get("answer", ""),
                "assessment": item.get("commentary", ""),
                "strengths": item.get("strengths") or [],
                "risks": item.get("risks") or [],
            }
        )
    evidence = [
        turn.get("text", "")
        for turn in status.get("transcript") or []
        if turn.get("role") == "candidate" and turn.get("text")
    ][:8]
    return {
        "recommendation": recommendation,
        "role_match_score": score,
        "conclusion": source.get("summary") or "本场有效信息不足，建议结合复试核验。",
        "candidate_evidence": evidence,
        "strengths": source.get("strengths") or [],
        "risks": source.get("weaknesses") or [],
        "verification_points": source.get("highlights", {}).get("alerts") or [],
        "suggested_second_round_questions": source.get("recommendations") or [],
        "question_assessments": qa,
    }
