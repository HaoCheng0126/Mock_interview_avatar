import json

from interview.interview_manager import InterviewManager
from interview.company_knowledge import prepare_company_knowledge
from interview.profile import CandidateProfile
from interview.roster import public_roster


def test_public_roster_filters_enterprise_and_repairs_locked_avatar():
    roster = {
        "selection_mode": "locked",
        "locked_avatar": "wang",
        "avatars": [
            {"slug": "chen", "name": "陈珊", "usage_type": "practice"},
            {"slug": "wang", "name": "王磊", "usage_type": "enterprise"},
        ],
    }
    public = public_roster(roster)
    assert [item["slug"] for item in public["avatars"]] == ["chen"]
    assert public["locked_avatar"] == "chen"


def test_internal_company_knowledge_is_explicitly_isolated(tmp_path):
    path = tmp_path / "enterprise.yaml"
    path.write_text(
        """
interview: {title: 企业面试}
interviewer: {name: 王磊}
positions: []
company_knowledge:
  entries:
    - id: public
      title: 支付产品
      category: products_business
      visibility: interview
      enabled: true
      content: PUBLIC_SENTINEL 支付成功率是业务重点。
    - id: internal
      title: 内部风险
      category: business_scenarios
      visibility: internal
      enabled: true
      content: INTERNAL_SENTINEL 仅招聘团队可见。
""",
        encoding="utf-8",
    )
    manager = InterviewManager(path)
    manager.apply_candidate_profile(CandidateProfile(target_role="支付产品经理"))

    public = json.dumps(manager.persona_context(), ensure_ascii=False)
    evaluator = json.dumps(
        manager.persona_context(include_internal=True), ensure_ascii=False
    )
    assert "PUBLIC_SENTINEL" in public
    assert "INTERNAL_SENTINEL" not in public
    assert "PUBLIC_SENTINEL" in evaluator
    assert "INTERNAL_SENTINEL" in evaluator


async def test_company_knowledge_is_prepared_once_into_two_bounded_channels(tmp_path):
    path = tmp_path / "enterprise.yaml"
    path.write_text(
        """
interview: {title: 企业面试}
interviewer: {name: 王磊}
company_knowledge:
  entries:
    - {id: p, title: 产品, category: products_business, visibility: interview, enabled: true, content: PUBLIC_RAW}
    - {id: i, title: 内部, category: other, visibility: internal, enabled: true, content: INTERNAL_RAW}
""",
        encoding="utf-8",
    )

    class Llm:
        calls = []

        async def generate_once(self, prompt, max_tokens):
            self.calls.append(prompt)
            return '{"interview":"公开压缩结果","internal":"内部压缩结果"}'

    llm = Llm()
    public, internal = await prepare_company_knowledge(
        path, CandidateProfile(target_role="产品经理"), llm
    )
    assert (public, internal) == ("公开压缩结果", "内部压缩结果")
    assert len(llm.calls) == 1
    assert "PUBLIC_RAW" in llm.calls[0]
    assert "INTERNAL_RAW" in llm.calls[0]
