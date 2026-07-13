from __future__ import annotations

import json
from pathlib import Path


class JsonInterviewStore:
    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)

    def save_status(self, interview_id: str, status: dict) -> None:
        path = self._path_for(interview_id)
        path.write_text(
            json.dumps(status, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_status(self, interview_id: str) -> dict:
        path = self._path_for(interview_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def _path_for(self, interview_id: str) -> Path:
        safe_id = "".join(ch for ch in interview_id if ch.isalnum() or ch in "_-")
        return self._directory / f"{safe_id}.json"
