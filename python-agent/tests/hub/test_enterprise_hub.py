import json

from hub import hub
from interview.enterprise_store import EnterpriseStore


class JsonRequest:
    def __init__(self, body, **match_info):
        self._body = body
        self.match_info = match_info

    async def json(self):
        return self._body


class FormRequest:
    async def post(self):
        return {
            "name": "程昊",
            "contact": "18451901908",
            "source": "Boss",
            "resume_text": "负责数据产品和数字人项目，具备产品规划与数据分析经验。",
            "resume_file": None,
        }


async def test_candidate_api_returns_json_and_persists_resume(tmp_path, monkeypatch):
    store = EnterpriseStore(tmp_path / "enterprise.db")
    monkeypatch.setattr(hub, "_enterprise_store", store)

    response = await hub.enterprise_candidate_create_handler(FormRequest())
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["success"] is True
    candidate = store.get_candidate(payload["candidate"]["id"])
    assert candidate["name"] == "程昊"
    assert "数字人项目" in candidate["resume_text"]


async def test_position_crud_handlers_return_json(tmp_path, monkeypatch):
    store = EnterpriseStore(tmp_path / "enterprise.db")
    monkeypatch.setattr(hub, "_enterprise_store", store)

    created_response = await hub.enterprise_position_create_handler(
        JsonRequest({"title": "AI 产品经理", "jd": "负责 AI 产品规划与交付"})
    )
    created = json.loads(created_response.text)["position"]
    assert created_response.status == 200

    updated_response = await hub.enterprise_position_update_handler(
        JsonRequest(
            {"title": "数字人 AI 产品经理", "jd": "负责数字人产品规划与商业化"},
            position_id=created["id"],
        )
    )
    updated = json.loads(updated_response.text)["position"]
    assert updated["title"] == "数字人 AI 产品经理"

    deleted_response = await hub.enterprise_position_delete_handler(
        JsonRequest({}, position_id=created["id"])
    )
    assert json.loads(deleted_response.text) == {"success": True}
