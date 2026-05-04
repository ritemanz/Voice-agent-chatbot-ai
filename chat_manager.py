"""
Persistent multi-chat storage.

Each chat session is a JSON file under ``data/chats/<session_id>.json``.
A session has the schema::

    {
      "id": "...",
      "title": "...",
      "created_at": "ISO8601",
      "updated_at": "ISO8601",
      "messages": [
          {"role": "user"|"assistant"|"system"|"tool",
           "content": "...",
           "timestamp": "ISO8601",
           "rating": "good"|"bad"|null,
           "audio_url": "/audio/<id>.mp3" | null}
      ]
    }
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"
CHATS_DIR = DATA_DIR / "chats"


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _ensure() -> None:
    CHATS_DIR.mkdir(parents=True, exist_ok=True)


def _path(session_id: str) -> Path:
    return CHATS_DIR / f"{session_id}.json"


def create_chat(title: str | None = None) -> dict[str, Any]:
    _ensure()
    sid = uuid.uuid4().hex[:12]
    chat = {
        "id": sid,
        "title": title or "New chat",
        "created_at": _now(),
        "updated_at": _now(),
        "messages": [],
        "notion_page_id": None,
        "notion_url": None,
        "notion_synced_count": 0,
    }
    _path(sid).write_text(json.dumps(chat, indent=2), encoding="utf-8")
    return chat


def load_chat(session_id: str) -> dict[str, Any] | None:
    _ensure()
    p = _path(session_id)
    if not p.exists():
        return None
    chat = json.loads(p.read_text(encoding="utf-8"))
    chat.setdefault("notion_page_id", None)
    chat.setdefault("notion_url", None)
    chat.setdefault("notion_synced_count", 0)
    return chat


def save_chat(chat: dict[str, Any]) -> None:
    _ensure()
    chat["updated_at"] = _now()
    _path(chat["id"]).write_text(json.dumps(chat, indent=2), encoding="utf-8")


def append_message(
    session_id: str,
    role: str,
    content: str,
    *,
    audio_url: str | None = None,
    tool_calls: list | None = None,
    tool_call_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    chat = load_chat(session_id)
    if chat is None:
        chat = create_chat()
        chat["id"] = session_id
    msg: dict[str, Any] = {
        "role": role,
        "content": content,
        "timestamp": _now(),
        "rating": None,
        "audio_url": audio_url,
    }
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if tool_call_id:
        msg["tool_call_id"] = tool_call_id
    if name:
        msg["name"] = name
    chat["messages"].append(msg)

    if chat["title"] in (None, "", "New chat") and role == "user" and content:
        chat["title"] = content.strip().split("\n")[0][:60]

    save_chat(chat)
    return msg


def list_chats() -> list[dict[str, Any]]:
    _ensure()
    out = []
    for p in sorted(CHATS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        out.append(
            {
                "id": data["id"],
                "title": data.get("title") or "Untitled",
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "message_count": len(data.get("messages", [])),
                "notion_url": data.get("notion_url"),
            }
        )
    return out


def delete_chat(session_id: str) -> bool:
    """Delete the local JSON for this chat. Notion is intentionally left alone."""
    p = _path(session_id)
    if not p.exists():
        return False
    try:
        p.unlink()
        return True
    except OSError:
        return False


def update_notion_state(
    session_id: str,
    *,
    page_id: str | None = None,
    url: str | None = None,
    synced_count: int | None = None,
) -> None:
    chat = load_chat(session_id)
    if chat is None:
        return
    if page_id is not None:
        chat["notion_page_id"] = page_id
    if url is not None:
        chat["notion_url"] = url
    if synced_count is not None:
        chat["notion_synced_count"] = synced_count
    save_chat(chat)


def rate_message(session_id: str, message_index: int, rating: str) -> dict[str, Any] | None:
    chat = load_chat(session_id)
    if chat is None:
        return None
    if not (0 <= message_index < len(chat["messages"])):
        return None
    chat["messages"][message_index]["rating"] = rating
    save_chat(chat)
    return chat["messages"][message_index]


def messages_for_llm(session_id: str) -> list[dict[str, Any]]:
    """Return chat history shaped for the OpenAI Chat Completions API."""
    chat = load_chat(session_id)
    if chat is None:
        return []
    cleaned: list[dict[str, Any]] = []
    for m in chat["messages"]:
        item: dict[str, Any] = {"role": m["role"], "content": m.get("content") or ""}
        if m.get("tool_calls"):
            item["tool_calls"] = m["tool_calls"]
        if m.get("tool_call_id"):
            item["tool_call_id"] = m["tool_call_id"]
        if m.get("name"):
            item["name"] = m["name"]
        cleaned.append(item)
    return cleaned
