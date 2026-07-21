from datetime import datetime, timedelta, timezone

from interview import enterprise_store as enterprise_store_module
from interview.enterprise_store import (
    EnterpriseStore,
    build_recruiting_report,
    get_enterprise_position,
    list_enterprise_positions,
)


def test_token_is_one_time_and_access_survives_store_restart(tmp_path):
    path = tmp_path / "enterprise.db"
    store = EnterpriseStore(path)
    candidate = store.create_candidate(name="张三", resume_text="五年产品经验")
    record, token = store.create_invite(
        "avatar-enterprise",
        7,
        {"slug": "avatar-enterprise", "name": "王磊"},
        candidate=candidate,
    )
    assert record["invite_token"] == token

    exchanged = store.exchange(token)
    assert exchanged is not None
    redeemed, access = exchanged
    assert redeemed["id"] == record["id"]
    assert store.exchange(token) is None

    restarted = EnterpriseStore(path)
    assert restarted.resolve_access(access)["id"] == record["id"]
    assert "token_hash" not in restarted.get(record["id"])


def test_pending_invite_link_can_be_regenerated_for_legacy_or_lost_links(tmp_path):
    store = EnterpriseStore(tmp_path / "enterprise.db")
    candidate = store.create_candidate(name="张三", resume_text="产品项目经验")
    record, old_token = store.create_invite(
        "avatar-enterprise", 7, {"name": "王磊"}, candidate=candidate
    )

    renewed_record, new_token = store.renew_invite(record["id"])
    assert new_token != old_token
    assert renewed_record["invite_token"] == new_token
    assert store.exchange(old_token) is None
    assert store.exchange(new_token) is not None


def test_complete_persists_report_and_delete_removes_record(tmp_path):
    store = EnterpriseStore(tmp_path / "enterprise.db")
    candidate = store.create_candidate(name="张三", resume_text="Python 项目经验")
    record, token = store.create_invite(
        "avatar-enterprise", 7, {"name": "王磊"}, candidate=candidate
    )
    _, access = store.exchange(token)
    store.mark_in_progress(
        record["id"],
        candidate_name="张三",
        candidate_contact="z@example.com",
        target_role="后端工程师",
    )
    completed = store.complete(
        record["id"],
        candidate_brief={"skills": ["Python"]},
        transcript=[{"role": "candidate", "text": "证据"}],
        report={"recommendation": "建议录用"},
        interview_id="iv_1",
    )
    assert completed["status"] == "completed"
    assert completed["candidate_brief"] == {"skills": ["Python"]}
    assert store.resolve_access(access)["status"] == "completed"
    assert store.delete(record["id"])
    assert store.get(record["id"]) is None


def test_invite_persists_recruiter_selected_position_and_candidate_cannot_override(tmp_path):
    position = get_enterprise_position("digital-human-ai-product")
    assert position is not None
    assert position["title"] == "数字人AI产品"
    assert "AI虚拟数字人产品C端商业化应用场景" in position["jd"]
    assert list_enterprise_positions()

    store = EnterpriseStore(tmp_path / "enterprise.db")
    candidate = store.create_candidate(
        name="张三",
        contact="z@example.com",
        source="内推",
        resume_filename="resume.pdf",
        resume_text="负责数字人产品商业化",
    )
    record, _ = store.create_invite(
        "avatar-enterprise",
        7,
        {"name": "王磊"},
        candidate=candidate,
        position_id=position["id"],
        target_role=position["title"],
        jd_text=position["jd"],
        position_snapshot=position,
        interview_config_snapshot={"interview": {"title": "企业面试"}},
    )
    assert record["position_id"] == position["id"]
    assert record["target_role"] == "数字人AI产品"
    assert record["jd_text"] == position["jd"]
    assert record["candidate_id"] == candidate["id"]
    assert record["candidate_snapshot"]["resume_text"] == "负责数字人产品商业化"
    assert record["position_snapshot"]["title"] == "数字人AI产品"
    assert record["interview_config_snapshot"]["interview"]["title"] == "企业面试"

    updated = store.mark_in_progress(
        record["id"],
        candidate_name="张三",
        candidate_contact="",
        target_role="候选人试图修改的岗位",
    )
    assert updated["target_role"] == "数字人AI产品"


def test_candidate_library_requires_resume_and_keeps_master_record_independent(tmp_path):
    store = EnterpriseStore(tmp_path / "enterprise.db")
    candidate = store.create_candidate(
        name="李四",
        contact="13800000000",
        source="官网",
        resume_filename="resume.docx",
        resume_text="负责过推荐系统产品",
    )
    summaries = store.list_candidates()
    assert summaries[0]["name"] == "李四"
    assert summaries[0]["resume_chars"] > 0
    assert "resume_text" not in summaries[0]
    assert store.get_candidate(candidate["id"])["resume_text"] == "负责过推荐系统产品"
    assert store.delete_candidate(candidate["id"])


def test_enterprise_positions_are_persistent_and_editable(tmp_path):
    path = tmp_path / "enterprise.db"
    store = EnterpriseStore(path)
    assert any(item["id"] == "digital-human-ai-product" for item in store.list_positions())

    created = store.create_position(title="海外增长产品经理", jd="负责海外增长和商业化")
    updated = store.update_position(
        created["id"],
        title="海外 AI 增长产品经理",
        jd="负责海外 AI 产品增长、商业化和数据分析",
    )
    assert updated["title"] == "海外 AI 增长产品经理"
    assert EnterpriseStore(path).get_position(created["id"])["jd"].startswith("负责海外 AI")
    assert store.delete_position(created["id"])


def test_every_database_operation_closes_its_connection(tmp_path, monkeypatch):
    original_connect = enterprise_store_module.sqlite3.connect
    opened = 0
    closed = 0

    class TrackedConnection:
        def __init__(self, connection):
            self.connection = connection

        @property
        def row_factory(self):
            return self.connection.row_factory

        @row_factory.setter
        def row_factory(self, value):
            self.connection.row_factory = value

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def close(self):
            nonlocal closed
            closed += 1
            return self.connection.close()

    def tracking_connect(*args, **kwargs):
        nonlocal opened
        opened += 1
        return TrackedConnection(original_connect(*args, **kwargs))

    monkeypatch.setattr(enterprise_store_module.sqlite3, "connect", tracking_connect)
    store = EnterpriseStore(tmp_path / "enterprise.db")
    candidate = store.create_candidate(name="连接测试", resume_text="候选人简历")
    store.list_candidates()
    store.get_candidate(candidate["id"])

    assert opened == closed


def test_recruiting_report_has_no_training_plan_fields():
    report = build_recruiting_report(
        {
            "finalReport": {
                "overallScore": 78,
                "summary": "岗位匹配良好",
                "strengths": ["证据充分"],
                "weaknesses": ["架构深度待核验"],
                "recommendations": ["复试追问容量规划"],
            },
            "transcript": [{"role": "candidate", "text": "我负责了缓存改造"}],
        }
    )
    assert report["recommendation"] == "建议录用"
    assert report["role_match_score"] == 78
    assert "learningPlan" not in report
    assert "参考答案" not in str(report)
