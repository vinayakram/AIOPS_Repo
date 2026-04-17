from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from core.settings import settings

STATE_FILE = settings.runs_dir / "current_state.json"
_STATE_LOCK = threading.RLock()


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    try:
        temp_path.replace(path)
    except PermissionError:
        path.write_text(content, encoding="utf-8")
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def load_state() -> dict[str, Any]:
    with _STATE_LOCK:
        if not STATE_FILE.exists():
            return {}
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}


def save_state(data: dict[str, Any]) -> None:
    with _STATE_LOCK:
        _atomic_write(STATE_FILE, json.dumps(data, indent=2))


def clear_state() -> None:
    with _STATE_LOCK:
        if STATE_FILE.exists():
            STATE_FILE.unlink()


def update_state(**kwargs: Any) -> dict[str, Any]:
    with _STATE_LOCK:
        state = load_state()
        state.update(kwargs)
        _atomic_write(STATE_FILE, json.dumps(state, indent=2))
        return state


def issue_dir(issue_id: str) -> Path:
    p = settings.runs_dir / issue_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def issue_state_path(issue_id: str) -> Path:
    return issue_dir(issue_id) / "state.json"


def run_log_path(issue_id: str) -> Path:
    return issue_dir(issue_id) / "run_progress.log"


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text)


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def clear_run_progress(issue_id: str) -> None:
    p = run_log_path(issue_id)
    if p.exists():
        p.unlink()


def append_progress(issue_id: str, message: str) -> None:
    line = f"[{_utc_now()}] {message.rstrip()}\n"
    append_text(run_log_path(issue_id), line)


def load_issue_state(issue_id: str) -> dict[str, Any]:
    path = issue_state_path(issue_id)
    with _STATE_LOCK:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}


def save_issue_state(issue_id: str, data: dict[str, Any]) -> None:
    path = issue_state_path(issue_id)
    with _STATE_LOCK:
        _atomic_write(path, json.dumps(data, indent=2))


def update_issue_state(issue_id: str, **kwargs: Any) -> dict[str, Any]:
    with _STATE_LOCK:
        path = issue_state_path(issue_id)
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        else:
            state = {}
        state.update(kwargs)
        state["last_updated"] = _utc_now()
        _atomic_write(path, json.dumps(state, indent=2))
        return state


def reset_for_issue(issue_id: str, issue_payload: dict[str, Any], status: str) -> None:
    clear_run_progress(issue_id)
    payload = {
        "issue": issue_payload,
        "status": status,
        "active_job": None,
        "job_phase": "idle",
        "job_error": "",
        "last_updated": _utc_now(),
        "latest_plan_file": None,
        "review_status": "not_requested",
        "review_decision": "",
        "review_notes": "",
        "pr_url": "",
    }
    save_issue_state(issue_id, payload)
    save_state(payload)


def reset_session_state() -> None:
    clear_state()


def get_next_plan_version(issue_id: str) -> int:
    run_dir = issue_dir(issue_id)
    versions: list[int] = []

    for path in run_dir.glob("plan_v*.md"):
        stem = path.stem
        suffix = stem.replace("plan_v", "", 1)
        if suffix.isdigit():
            versions.append(int(suffix))

    return (max(versions) + 1) if versions else 1


def set_latest_plan_file(issue_id: str, plan_filename: str) -> None:
    state = update_issue_state(issue_id, latest_plan_file=plan_filename)
    save_state(state)


def get_latest_plan_path(issue_id: str) -> Path | None:
    state = load_issue_state(issue_id)
    latest_plan_file = state.get("latest_plan_file")

    if latest_plan_file:
        candidate = issue_dir(issue_id) / str(latest_plan_file)
        if candidate.exists():
            return candidate

    run_dir = issue_dir(issue_id)
    versions: list[tuple[int, Path]] = []

    for path in run_dir.glob("plan_v*.md"):
        stem = path.stem
        suffix = stem.replace("plan_v", "", 1)
        if suffix.isdigit():
            versions.append((int(suffix), path))

    if not versions:
        legacy = run_dir / "plan.md"
        if legacy.exists():
            return legacy
        return None

    versions.sort(key=lambda item: item[0])
    return versions[-1][1]
