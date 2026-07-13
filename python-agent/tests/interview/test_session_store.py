from interview.session_store import JsonInterviewStore


def test_json_interview_store_saves_and_loads_status(tmp_path):
    store = JsonInterviewStore(tmp_path)

    store.save_status("iv_test", {"state": "completed", "transcript": []})

    assert store.load_status("iv_test")["state"] == "completed"
    assert (tmp_path / "iv_test.json").exists()
