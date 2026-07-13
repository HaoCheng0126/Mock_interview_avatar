"""Candidate profile intake for the interview page.

The interview page lets the candidate supply a job title, a JD, and a resume
(file upload or pasted text) before starting. Those land in the same
config/interview.yaml the whole pipeline already reads:

  * job title   → ``candidate.target_role``
  * JD text     → knowledge entry titled 岗位 JD
  * resume text → knowledge entry titled 候选人简历

Entries are upserted by title — other knowledge entries, questions, prompts
and every other section stay untouched. The next session picks the profile up
automatically (the agent re-reads the YAML per session).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import yaml

JD_ENTRY_TITLE = "岗位 JD"
RESUME_ENTRY_TITLE = "候选人简历"
SUPPORTED_RESUME_EXTS = (".pdf", ".docx", ".txt", ".md")
MAX_TEXT_CHARS = 20000

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "interview.yaml"


def extract_resume_text(filename: str, data: bytes) -> str:
    """Extract plain text from an uploaded resume file."""
    ext = Path(filename or "").suffix.lower()
    if ext in (".txt", ".md"):
        text = data.decode("utf-8", errors="replace")
    elif ext == ".pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            raise ValueError(f"PDF 解析失败：{e}")
    elif ext == ".docx":
        from docx import Document

        try:
            document = Document(io.BytesIO(data))
            text = "\n".join(p.text for p in document.paragraphs)
        except Exception as e:
            raise ValueError(f"Word 文档解析失败：{e}")
    else:
        supported = " / ".join(SUPPORTED_RESUME_EXTS)
        raise ValueError(f"暂不支持 {ext or '该'} 格式，请上传 {supported}，或直接粘贴文本")

    text = text.strip()
    if not text:
        raise ValueError("未能从文件中提取到文本（可能是扫描件），请直接粘贴简历内容")
    return text[:MAX_TEXT_CHARS]


def apply_profile(
    *,
    target_role: str = "",
    jd_text: str = "",
    resume_text: str = "",
    path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, Any]:
    """Apply non-empty profile fields to interview.yaml and return the summary."""
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    if target_role.strip():
        doc.setdefault("candidate", {})["target_role"] = target_role.strip()
    if jd_text.strip():
        _upsert_entry(doc, JD_ENTRY_TITLE, jd_text.strip()[:MAX_TEXT_CHARS])
    if resume_text.strip():
        _upsert_entry(doc, RESUME_ENTRY_TITLE, resume_text.strip()[:MAX_TEXT_CHARS])

    Path(path).write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )
    return read_profile(path)


def read_profile(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = (doc.get("knowledge") or {}).get("entries") or []
    by_title = {
        str(item.get("title") or ""): str(item.get("content") or "")
        for item in entries
        if isinstance(item, dict)
    }
    return {
        "target_role": str((doc.get("candidate") or {}).get("target_role") or ""),
        "jd_chars": len(by_title.get(JD_ENTRY_TITLE, "")),
        "resume_chars": len(by_title.get(RESUME_ENTRY_TITLE, "")),
    }


def _upsert_entry(doc: dict[str, Any], title: str, content: str) -> None:
    knowledge = doc.setdefault("knowledge", {})
    entries = knowledge.setdefault("entries", [])
    for item in entries:
        if isinstance(item, dict) and str(item.get("title") or "") == title:
            item["content"] = content
            item["enabled"] = True
            return
    entries.append({"title": title, "content": content, "enabled": True})
