"""
Multi-chat storage with two interchangeable backends:

1. **Notion-backed (default when Notion is configured)** — chats live only
   in an in-memory cache (``_MEM``) inside this process. The cache is
   hydrated from Notion at startup, and Notion is treated as the durable
   store (chat content is auto-pushed to Notion after every turn by
   ``Voice_Agent_Chatbot._run_chat_with_tools`` via ``tools._sync_chat_to_notion``).
   Nothing is written to ``data/chats/`` on disk.

2. **Local-file fallback (when Notion is NOT configured)** — chats are
   serialised to ``data/chats/<id>.json`` exactly as before, so the app
   still works offline / without a Notion account.

The mode is decided per-call (not cached) so flipping the env vars and
restarting is enough to switch backends.

Schema (both backends)::

    {
      "id": "...",
      "title": "...",
      "created_at": "ISO8601",
      "updated_at": "ISO8601",
      "messages": [...],
      "notion_page_id": str | null,
      "notion_url": str | null,
      "notion_synced_count": int,
    }
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"
CHATS_DIR = DATA_DIR / "chats"
DELETED_DIR = CHATS_DIR / "_deleted"

# In-memory store used when Notion is configured. Keyed by session_id.
_MEM: dict[str, dict[str, Any]] = {}
_MEM_DELETED: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Mode detection
# ---------------------------------------------------------------------------
def _notion_configured() -> bool:
    return bool(os.getenv("NOTION_API_KEY") and os.getenv("NOTION_PARENT_PAGE_ID"))


def use_memory_only() -> bool:
    """True iff chats should live only in memory + Notion (no local disk)."""
    return _notion_configured()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _ensure_disk() -> None:
    CHATS_DIR.mkdir(parents=True, exist_ok=True)
    DELETED_DIR.mkdir(parents=True, exist_ok=True)


def _path(session_id: str) -> Path:
    return CHATS_DIR / f"{session_id}.json"


def _deleted_path(session_id: str) -> Path:
    return DELETED_DIR / f"{session_id}.json"


def _new_chat_dict(session_id: str, title: str | None) -> dict[str, Any]:
    return {
        "id": session_id,
        "title": title or "New chat",
        "created_at": _now(),
        "updated_at": _now(),
        "messages": [],
        "notion_page_id": None,
        "notion_url": None,
        "notion_synced_count": 0,
    }


def _normalise(chat: dict[str, Any]) -> dict[str, Any]:
    chat.setdefault("notion_page_id", None)
    chat.setdefault("notion_url", None)
    chat.setdefault("notion_synced_count", 0)
    chat.setdefault("messages", [])
    return chat


# ---------------------------------------------------------------------------
# CRUD: dispatches to the active backend
# ---------------------------------------------------------------------------
def create_chat(title: str | None = None) -> dict[str, Any]:
    sid = uuid.uuid4().hex[:12]
    chat = _new_chat_dict(sid, title)

    if use_memory_only():
        _MEM[sid] = chat
        return copy.deepcopy(chat)

    _ensure_disk()
    _path(sid).write_text(json.dumps(chat, indent=2), encoding="utf-8")
    return chat


def load_chat(session_id: str) -> dict[str, Any] | None:
    if use_memory_only():
        chat = _MEM.get(session_id)
        return copy.deepcopy(_normalise(chat)) if chat is not None else None

    _ensure_disk()
    p = _path(session_id)
    if not p.exists():
        return None
    return _normalise(json.loads(p.read_text(encoding="utf-8")))


def save_chat(chat: dict[str, Any]) -> None:
    chat["updated_at"] = _now()

    if use_memory_only():
        _MEM[chat["id"]] = copy.deepcopy(_normalise(chat))
        return

    _ensure_disk()
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
        # In memory mode, rewrite the auto-generated id to the requested one.
        if use_memory_only():
            old = chat["id"]
            _MEM.pop(old, None)
            _MEM[session_id] = chat
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
    if use_memory_only():
        chats = sorted(
            _MEM.values(),
            key=lambda c: c.get("updated_at") or "",
            reverse=True,
        )
    else:
        _ensure_disk()
        chats = []
        for p in sorted(CHATS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                chats.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                continue

    return [
        {
            "id": data["id"],
            "title": data.get("title") or "Untitled",
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "message_count": len(data.get("messages", [])),
            "notion_url": data.get("notion_url"),
        }
        for data in chats
    ]


def delete_chat(session_id: str) -> bool:
    """Soft-delete the chat. Notion page is intentionally left alone."""
    if use_memory_only():
        chat = _MEM.pop(session_id, None)
        if chat is None:
            return False
        _MEM_DELETED[session_id] = chat
        return True

    _ensure_disk()
    p = _path(session_id)
    if not p.exists():
        return False
    try:
        target = _deleted_path(session_id)
        if target.exists():
            target.unlink()
        p.replace(target)
        return True
    except OSError:
        return False


def restore_chat(session_id: str) -> dict[str, Any] | None:
    """Undo a soft-delete. Returns the restored chat or None."""
    if use_memory_only():
        chat = _MEM_DELETED.pop(session_id, None)
        if chat is None:
            return None
        _MEM[session_id] = chat
        return copy.deepcopy(chat)

    _ensure_disk()
    src = _deleted_path(session_id)
    dst = _path(session_id)
    if not src.exists():
        return None
    try:
        if dst.exists():
            dst.unlink()
        src.replace(dst)
    except OSError:
        return None
    return load_chat(session_id)


def has_deleted_backup(session_id: str) -> bool:
    if use_memory_only():
        return session_id in _MEM_DELETED
    return _deleted_path(session_id).exists()


def get_deleted_metadata(session_id: str) -> dict[str, Any] | None:
    """Return the chat dict from the soft-delete trash, without restoring it.
    Used to recover ``notion_page_id`` if the local backup is gone."""
    if use_memory_only():
        chat = _MEM_DELETED.get(session_id)
        return copy.deepcopy(chat) if chat is not None else None

    p = _deleted_path(session_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def write_chat_from_messages(
    session_id: str,
    title: str,
    messages: list[dict[str, Any]],
    *,
    notion_page_id: str | None = None,
    notion_url: str | None = None,
) -> dict[str, Any]:
    """Create / overwrite a chat from an externally-sourced message list
    (e.g. a Notion page). Used by both startup hydration and undo-restore."""
    chat = {
        "id": session_id,
        "title": title or "Restored chat",
        "created_at": _now(),
        "updated_at": _now(),
        "messages": messages,
        "notion_page_id": notion_page_id,
        "notion_url": notion_url,
        "notion_synced_count": len(messages),
    }
    if use_memory_only():
        _MEM[session_id] = copy.deepcopy(chat)
        return copy.deepcopy(chat)

    _ensure_disk()
    _path(session_id).write_text(json.dumps(chat, indent=2), encoding="utf-8")
    return chat


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


# ---------------------------------------------------------------------------
# Test / housekeeping helpers
# ---------------------------------------------------------------------------
def _reset_memory_for_tests() -> None:
    _MEM.clear()
    _MEM_DELETED.clear()
