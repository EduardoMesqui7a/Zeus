from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SESSION_FILE = Path(__file__).resolve().parents[1] / ".zeus_session.json"


def load_saved_session() -> dict[str, Any]:
    if not SESSION_FILE.exists():
        return {}
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_saved_session(data: dict[str, Any]) -> None:
    SESSION_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_token(token: str, *, username: str | None = None) -> None:
    payload: dict[str, Any] = {"auth_token": token}
    if username:
        payload["username"] = username
    save_saved_session(payload)


def clear_saved_session() -> None:
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
