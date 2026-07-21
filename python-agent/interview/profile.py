"""Candidate profile intake for the interview page.

The candidate supplies a job title, a JD, and a resume (file upload or pasted
text) right before the interview. This module ONLY parses that input into an
in-memory :class:`CandidateProfile` — it is never written to disk. The active
session overlays it onto its own config
(:meth:`InterviewManager.apply_candidate_profile`) and drops it when the
interview ends, so the backend keeps no candidate data and imposes no
restriction on role or industry. Each interview is driven purely by whatever
the candidate uploads at that moment.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, replace
from pathlib import Path

SUPPORTED_RESUME_EXTS = (".pdf", ".docx", ".txt", ".md")
MAX_TEXT_CHARS = 20000


@dataclass(frozen=True)
class CandidateProfile:
    """One candidate's ephemeral, per-session inputs. Never persisted."""

    target_role: str = ""
    jd_text: str = ""
    resume_text: str = ""

    def merge(
        self, *, target_role: str = "", jd_text: str = "", resume_text: str = ""
    ) -> "CandidateProfile":
        """Return a copy with only the non-empty fields overwritten.

        The prep form is write-only: it clears the JD/resume boxes after a save
        and may re-post with just the field the candidate edited, so an empty
        incoming field must leave the existing value intact.
        """
        return replace(
            self,
            target_role=target_role.strip() or self.target_role,
            jd_text=jd_text.strip()[:MAX_TEXT_CHARS] or self.jd_text,
            resume_text=resume_text.strip()[:MAX_TEXT_CHARS] or self.resume_text,
        )

    @property
    def summary(self) -> dict:
        """Non-sensitive summary for the prep UI — counts, never raw content."""
        return {
            "target_role": self.target_role,
            "jd_chars": len(self.jd_text),
            "resume_chars": len(self.resume_text),
            "has_jd": bool(self.jd_text.strip()),
        }

    def is_empty(self) -> bool:
        return not (self.target_role or self.jd_text or self.resume_text)


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
