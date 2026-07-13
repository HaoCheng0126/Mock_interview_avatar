"""Tests for interview.profile — resume extraction + profile upsert into YAML."""

import io

import pytest
import yaml

from interview.interview_manager import InterviewManager
from interview.profile import (
    JD_ENTRY_TITLE,
    RESUME_ENTRY_TITLE,
    apply_profile,
    extract_resume_text,
    read_profile,
)


BASE_YAML = """
interview:
  title: "测试面试"
interviewer:
  name: "测试面试官"
candidate:
  target_role: "原始岗位"
knowledge:
  entries:
    - title: "公司介绍"
      content: "一家做数字人的公司"
question_sets:
  - id: q1
    title: "问题一"
    prompt: "请介绍你自己。"
"""


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "interview.yaml"
    path.write_text(BASE_YAML, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# extract_resume_text
# ---------------------------------------------------------------------------


def test_extract_txt_and_md():
    assert extract_resume_text("resume.txt", "三年后端经验".encode()) == "三年后端经验"
    assert extract_resume_text("resume.md", b"# Resume\nPython") == "# Resume\nPython"


def test_extract_unsupported_ext_raises():
    with pytest.raises(ValueError, match="暂不支持"):
        extract_resume_text("resume.jpg", b"xx")


def test_extract_empty_pdf_raises():
    from pypdf import PdfWriter

    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(buf)
    with pytest.raises(ValueError, match="未能从文件中提取到文本"):
        extract_resume_text("resume.pdf", buf.getvalue())


def test_extract_docx():
    from docx import Document

    buf = io.BytesIO()
    document = Document()
    document.add_paragraph("五年 Python 开发经验")
    document.add_paragraph("负责订单系统")
    document.save(buf)
    text = extract_resume_text("resume.docx", buf.getvalue())
    assert "五年 Python 开发经验" in text
    assert "负责订单系统" in text


# ---------------------------------------------------------------------------
# apply_profile / read_profile
# ---------------------------------------------------------------------------


def test_apply_profile_upserts_and_preserves(config_path):
    summary = apply_profile(
        target_role="测试工程师",
        jd_text="负责后端服务",
        resume_text="三年经验",
        path=config_path,
    )
    assert summary == {"target_role": "测试工程师", "jd_chars": 6, "resume_chars": 4}

    doc = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    titles = [e["title"] for e in doc["knowledge"]["entries"]]
    assert titles == ["公司介绍", JD_ENTRY_TITLE, RESUME_ENTRY_TITLE]
    assert doc["candidate"]["target_role"] == "测试工程师"
    assert len(doc["question_sets"]) == 1

    # second apply replaces content instead of duplicating
    apply_profile(jd_text="新版 JD 内容", path=config_path)
    doc = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    jd_entries = [e for e in doc["knowledge"]["entries"] if e["title"] == JD_ENTRY_TITLE]
    assert len(jd_entries) == 1
    assert jd_entries[0]["content"] == "新版 JD 内容"


def test_apply_profile_empty_fields_untouched(config_path):
    apply_profile(target_role="  ", jd_text="", resume_text="", path=config_path)
    doc = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert doc["candidate"]["target_role"] == "原始岗位"
    assert len(doc["knowledge"]["entries"]) == 1


def test_profile_feeds_system_prompt(config_path):
    apply_profile(
        target_role="测试工程师",
        jd_text="负责后端服务",
        resume_text="三年经验",
        path=config_path,
    )
    manager = InterviewManager(config_path)
    system = manager.build_system_prompt()
    assert "测试工程师" in system
    assert "负责后端服务" in system
    assert "三年经验" in system


def test_read_profile_reports_state(config_path):
    assert read_profile(config_path) == {
        "target_role": "原始岗位",
        "jd_chars": 0,
        "resume_chars": 0,
    }
