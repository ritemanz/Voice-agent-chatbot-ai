"""
External tool implementations exposed to the LLM via function-calling.

Currently:
- search_arxiv(query, max_results=3): returns top-N relevant arXiv papers.
- sync_to_notion(session_id, content): appends a chat session to Notion.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

import arxiv
from notion_client import Client as NotionClient

import chat_manager

# A chat page's Notion title looks like "<title>  (<session_id>)" - see
# create_notion_page. Extract that trailing session id back out so we can
# rebuild the chat list from Notion.
_SESSION_ID_IN_TITLE = re.compile(r"\(([0-9a-fA-F]{8,32})\)\s*$")


# ---------------------------------------------------------------------------
# ArXiv search
# ---------------------------------------------------------------------------
def search_arxiv(query: str, max_results: int = 3) -> list[dict[str, Any]]:
    """Search arXiv and return the top `max_results` papers as dicts.

    Each dict contains: title, authors, summary, url, published.
    A short ``excerpt`` is also produced so the LLM can quote it directly.
    """
    if not query or not query.strip():
        return []

    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.Relevance,
        sort_order=arxiv.SortOrder.Descending,
    )

    client = arxiv.Client(page_size=max_results, delay_seconds=1, num_retries=3)
    results: list[dict[str, Any]] = []
    for paper in client.results(search):
        summary = (paper.summary or "").strip().replace("\n", " ")
        excerpt = summary[:600] + ("..." if len(summary) > 600 else "")
        results.append(
            {
                "title": paper.title.strip(),
                "authors": [a.name for a in paper.authors],
                "summary": summary,
                "excerpt": excerpt,
                "url": paper.entry_id,
                "pdf_url": paper.pdf_url,
                "published": paper.published.isoformat() if paper.published else None,
            }
        )
    return results


# ---------------------------------------------------------------------------
# Notion sync
# ---------------------------------------------------------------------------
def _notion_client() -> NotionClient | None:
    api_key = os.getenv("NOTION_API_KEY")
    if not api_key:
        return None
    return NotionClient(auth=api_key)


def _chunk_text(text: str, size: int = 1900) -> list[str]:
    """Notion rich_text blocks have a 2000-char limit; chunk safely."""
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def _paragraph_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for chunk in _chunk_text(text):
        blocks.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"type": "text", "text": {"content": chunk}}
                    ]
                },
            }
        )
    return blocks


def _heading_block(text: str, level: int = 2) -> dict[str, Any]:
    h_type = f"heading_{level}"
    return {
        "object": "block",
        "type": h_type,
        h_type: {
            "rich_text": [{"type": "text", "text": {"content": text[:1900]}}]
        },
    }


def _messages_to_blocks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        # Skip tool-call plumbing - those are noisy and not interesting on Notion.
        if role == "tool":
            continue
        if role == "assistant" and not (msg.get("content") or "").strip():
            continue
        ts = msg.get("timestamp", "")
        header = f"{role.capitalize()}" + (f"  ·  {ts}" if ts else "")
        blocks.append(_heading_block(header, level=3))
        blocks.extend(_paragraph_blocks(str(msg.get("content", ""))))
    return blocks


def create_notion_page(
    session_id: str,
    title: str,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a fresh Notion page for this chat session."""
    notion = _notion_client()
    parent_id = os.getenv("NOTION_PARENT_PAGE_ID")
    if notion is None or not parent_id:
        return {
            "ok": False,
            "page_id": None,
            "url": None,
            "error": "NOTION_API_KEY or NOTION_PARENT_PAGE_ID not configured.",
        }
    page_title = f"{title or 'Chat'}  ({session_id})"
    try:
        page = notion.pages.create(
            parent={"page_id": parent_id},
            properties={
                "title": [{"type": "text", "text": {"content": page_title[:1900]}}]
            },
            children=[
                _heading_block(
                    f"Started {datetime.utcnow().isoformat(timespec='seconds')}Z",
                    level=2,
                ),
                *_messages_to_blocks(messages),
            ],
        )
        return {"ok": True, "page_id": page["id"], "url": page.get("url"), "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "page_id": None, "url": None, "error": str(exc)}


def _rich_text_to_plain(rich: list[dict[str, Any]]) -> str:
    return "".join(item.get("plain_text", "") for item in rich or [])


def list_notion_chat_pages(parent_id: str | None = None) -> dict[str, Any]:
    """List every direct child page of the configured Notion parent page that
    looks like one of our chat pages (title ends with ``(<session_id>)``).

    Returns ``{"ok": bool, "chats": [{session_id, page_id, url, title}], "error": ...}``.
    """
    notion = _notion_client()
    parent = parent_id or os.getenv("NOTION_PARENT_PAGE_ID")
    if notion is None or not parent:
        return {"ok": False, "chats": [], "error": "Notion not configured."}

    try:
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            kwargs: dict[str, Any] = {"block_id": parent, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = notion.blocks.children.list(**kwargs)
            results.extend(resp.get("results", []))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")

        chats: list[dict[str, Any]] = []
        for blk in results:
            if blk.get("type") != "child_page":
                continue
            title = (blk.get("child_page") or {}).get("title", "") or ""
            match = _SESSION_ID_IN_TITLE.search(title)
            if not match:
                # A page in the parent that wasn't created by our sync code -
                # ignore it so we don't pollute the sidebar with unrelated
                # Notion pages.
                continue
            session_id = match.group(1)
            page_id = blk.get("id", "").replace("-", "")
            display_title = _SESSION_ID_IN_TITLE.sub("", title).strip()
            chats.append(
                {
                    "session_id": session_id,
                    "page_id": page_id,
                    "url": f"https://www.notion.so/{page_id}",
                    "title": display_title or "Chat",
                }
            )
        return {"ok": True, "chats": chats, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "chats": [], "error": str(exc)}


def fetch_notion_page_messages(page_id: str) -> dict[str, Any]:
    """Read all blocks of a Notion page and reconstruct chat messages.

    Looks for the heading_3 / paragraph pairs that ``_messages_to_blocks``
    writes and stitches them back into ``[{role, content, timestamp}, ...]``.
    """
    notion = _notion_client()
    if notion is None:
        return {"ok": False, "messages": [], "error": "Notion not configured."}

    try:
        blocks: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            kwargs: dict[str, Any] = {"block_id": page_id, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = notion.blocks.children.list(**kwargs)
            blocks.extend(resp.get("results", []))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")

        messages: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for blk in blocks:
            btype = blk.get("type")
            if btype == "heading_3":
                # Flush previous message before starting a new one.
                if current is not None:
                    messages.append(current)
                header = _rich_text_to_plain(blk["heading_3"].get("rich_text", []))
                # Format: "Role  ·  timestamp"
                role_raw, _, ts = header.partition("·")
                role = role_raw.strip().lower() or "assistant"
                if role not in {"user", "assistant", "system"}:
                    role = "assistant"
                current = {
                    "role": role,
                    "content": "",
                    "timestamp": ts.strip(),
                    "rating": None,
                    "audio_url": None,
                }
            elif btype == "paragraph" and current is not None:
                text = _rich_text_to_plain(blk["paragraph"].get("rich_text", []))
                current["content"] = (current["content"] + text) if current["content"] == "" else current["content"] + text
            # heading_2 / other block types from create_notion_page (the
            # "Started ..." banner) are intentionally ignored.
        if current is not None:
            messages.append(current)

        return {"ok": True, "messages": messages, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "messages": [], "error": str(exc)}


def append_to_notion_page(
    page_id: str, messages: list[dict[str, Any]]
) -> dict[str, Any]:
    """Append the given messages to an existing Notion page."""
    notion = _notion_client()
    if notion is None or not page_id:
        return {"ok": False, "error": "Notion not configured or no page_id."}
    new_blocks = _messages_to_blocks(messages)
    if not new_blocks:
        return {"ok": True, "appended": 0}
    try:
        # Notion's block-append endpoint takes max 100 children at a time.
        appended = 0
        for i in range(0, len(new_blocks), 90):
            chunk = new_blocks[i : i + 90]
            notion.blocks.children.append(block_id=page_id, children=chunk)
            appended += len(chunk)
        return {"ok": True, "appended": appended}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def sync_to_notion(
    session_id: str,
    content: dict[str, Any] | str,
    *,
    page_id: str | None = None,
    synced_count: int = 0,
) -> dict[str, Any]:
    """Create-or-append a Notion page for this chat.

    - If ``page_id`` is None, creates a new page from all messages.
    - Otherwise, appends only ``messages[synced_count:]`` to the existing page.
    """
    if isinstance(content, str):
        title = f"Chat {session_id}"
        messages = [{"role": "assistant", "content": content, "timestamp": ""}]
    else:
        title = content.get("title") or f"Chat {session_id}"
        messages = content.get("messages", [])

    if not page_id:
        result = create_notion_page(session_id, title, messages)
        if result.get("ok"):
            result["synced_count"] = len(messages)
        return result

    new_msgs = messages[synced_count:]
    result = append_to_notion_page(page_id, new_msgs)
    if result.get("ok"):
        result["synced_count"] = len(messages)
        result["page_id"] = page_id
    return result


# ---------------------------------------------------------------------------
# OpenAI tool schema (function-calling)
# ---------------------------------------------------------------------------
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_arxiv",
            "description": (
                "Search arXiv for the top relevant papers given a natural "
                "language query. Returns title, authors, summary, excerpt "
                "and URL for each paper. Use this whenever the user asks a "
                "research question that benefits from citing recent papers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query, e.g. 'retrieval augmented generation'.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "How many papers to return (default 3).",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_to_notion",
            "description": (
                "Save / sync the current chat to Notion. On first call this "
                "creates a fresh Notion page; subsequent calls append only "
                "the newly-added messages to the existing page. Call this "
                "when the user explicitly asks to save / sync / export the "
                "conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Chat session id. Optional - the server already knows the active session.",
                    }
                },
                "required": [],
            },
        },
    },
]


def _sync_chat_to_notion(chat_state: dict[str, Any]) -> dict[str, Any]:
    """Sync (create-or-append) the chat referenced by ``chat_state`` to Notion.

    ``chat_state`` is expected to come from ``chat_manager.load_chat`` and to
    contain at least ``session_id``/``id``, ``title``, ``messages``,
    ``notion_page_id``, ``notion_synced_count``. The persisted notion state
    is updated via ``chat_manager.update_notion_state`` on success.
    """
    session_id = chat_state.get("session_id") or chat_state.get("id")
    if not session_id:
        return {"ok": False, "error": "missing session_id"}

    fresh = chat_manager.load_chat(session_id) or chat_state
    title = fresh.get("title") or f"Chat {session_id}"
    messages = fresh.get("messages", [])
    page_id = fresh.get("notion_page_id")
    synced_count = int(fresh.get("notion_synced_count") or 0)

    result = sync_to_notion(
        session_id=session_id,
        content={"title": title, "messages": messages},
        page_id=page_id,
        synced_count=synced_count,
    )

    if result.get("ok"):
        chat_manager.update_notion_state(
            session_id,
            page_id=result.get("page_id") or page_id,
            url=result.get("url"),
            synced_count=result.get("synced_count", len(messages)),
        )
    return result


def dispatch_tool(name: str, arguments: dict[str, Any], chat_state: dict[str, Any]) -> Any:
    """Run a tool call requested by the LLM and return its JSON-serialisable result."""
    if name == "search_arxiv":
        return search_arxiv(
            query=arguments.get("query", ""),
            max_results=int(arguments.get("max_results", 3) or 3),
        )
    if name == "sync_to_notion":
        return _sync_chat_to_notion(chat_state)
    return {"error": f"Unknown tool '{name}'."}
