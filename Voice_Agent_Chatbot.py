"""
Voice_Agent_Chatbot.py - voice-driven research assistant.

Run:
    python Voice_Agent_Chatbot.py
then open http://localhost:8000 in your browser.

Pipeline:
    [Mic] -> Whisper ASR -> GPT (function-calling: search_arxiv / sync_to_notion)
         -> TTS -> playback
         -> good/bad feedback feeds the continuous-learning feature memory
         -> entire dialogue persisted locally + synced to Notion
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

import chat_manager
import learning
from tools import (
    TOOL_SCHEMAS,
    _sync_chat_to_notion,
    dispatch_tool,
    fetch_notion_page_messages,
    list_notion_chat_pages,
)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
load_dotenv()

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
AUDIO_DIR = DATA_DIR / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_ASR_MODEL = os.getenv("OPENAI_ASR_MODEL", "whisper-1")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "nova")

_openai_client: OpenAI | None = None


def get_openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise HTTPException(500, "OPENAI_API_KEY is not set in .env")
        _openai_client = OpenAI(api_key=api_key)
    return _openai_client


SYSTEM_PROMPT_BASE = (
    "You are a voice-driven research assistant. The user's question often "
    "comes from speech-to-text, so be tolerant of small transcription errors. "
    "When the user asks a research / scientific question, you SHOULD call "
    "the `search_arxiv` tool and condense the retrieved excerpts into a clear, "
    "well-cited answer. When the user asks to save / sync the conversation, "
    "call `sync_to_notion`. Always cite sources with their arXiv URLs when "
    "you used search results. Keep answers focused and easy to listen to "
    "(they may be read aloud). The conversation is automatically saved to "
    "Notion in the background, so the user does not need to ask for that.\n\n"
    "LANGUAGE: Detect the language of the user's most recent message and "
    "ALWAYS reply in that exact same language. If the user writes in "
    "Spanish, reply in Spanish; in French, reply in French; in Mandarin, "
    "reply in Mandarin; etc. Translate any English material you retrieve "
    "(including arXiv excerpts) into the user's language before quoting it, "
    "but keep paper titles, author names, and URLs in their original form. "
    "If the user explicitly asks for a different language, switch to that "
    "language instead. If the language is genuinely ambiguous, default to "
    "English."
)


def build_system_prompt() -> str:
    guidance = learning.build_guidance()
    if guidance:
        return SYSTEM_PROMPT_BASE + "\n\n" + guidance
    return SYSTEM_PROMPT_BASE


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Voice Agent Chatbot")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")


# ---------------------------------------------------------------------------
# 1) Whisper ASR  (audio -> text)
# ---------------------------------------------------------------------------
@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...)) -> dict[str, Any]:
    raw = await audio.read()
    if not raw:
        raise HTTPException(400, "Empty audio upload.")
    suffix = Path(audio.filename or "rec.webm").suffix or ".webm"
    tmp = AUDIO_DIR / f"in_{uuid.uuid4().hex}{suffix}"
    tmp.write_bytes(raw)
    try:
        client = get_openai()
        with tmp.open("rb") as fh:
            tr = client.audio.transcriptions.create(
                model=OPENAI_ASR_MODEL,
                file=fh,
            )
        return {"text": tr.text}
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 2) Chat (OpenAI w/ function calling -> orchestrates tools)
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    session_id: str | None = None
    user_text: str
    speak: bool = True


def _run_chat_with_tools(session_id: str) -> dict[str, Any]:
    """Drive the multi-turn function-calling loop until the model is done."""
    client = get_openai()
    history = chat_manager.messages_for_llm(session_id)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt()},
        *history,
    ]

    final_text: str = ""
    tool_trace: list[dict[str, Any]] = []
    chat_state = chat_manager.load_chat(session_id) or {"session_id": session_id}
    chat_state["session_id"] = session_id

    for _ in range(6):
        resp = client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
        )
        msg = resp.choices[0].message
        tool_calls = msg.tool_calls or []

        if not tool_calls:
            final_text = msg.content or ""
            chat_manager.append_message(session_id, "assistant", final_text)
            break

        # Persist the assistant tool-call message (no content, just calls).
        serialized_calls = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]
        chat_manager.append_message(
            session_id, "assistant", msg.content or "", tool_calls=serialized_calls
        )
        messages.append(
            {"role": "assistant", "content": msg.content or "", "tool_calls": serialized_calls}
        )

        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            chat_state = chat_manager.load_chat(session_id) or chat_state
            chat_state["session_id"] = session_id
            result = dispatch_tool(tc.function.name, args, chat_state)
            tool_trace.append({"name": tc.function.name, "args": args, "result": result})
            tool_msg_content = json.dumps(result, ensure_ascii=False)
            chat_manager.append_message(
                session_id,
                "tool",
                tool_msg_content,
                tool_call_id=tc.id,
                name=tc.function.name,
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": tool_msg_content,
                }
            )
    else:
        final_text = "(stopped after too many tool iterations)"
        chat_manager.append_message(session_id, "assistant", final_text)

    return {"text": final_text, "tool_trace": tool_trace}


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict[str, Any]:
    session_id = req.session_id
    if not session_id or chat_manager.load_chat(session_id) is None:
        session_id = chat_manager.create_chat()["id"]

    chat_manager.append_message(session_id, "user", req.user_text)
    result = _run_chat_with_tools(session_id)

    audio_url: str | None = None
    if req.speak and result["text"]:
        try:
            audio_url = _tts_to_file(result["text"])
            # attach audio_url to last assistant message
            chat = chat_manager.load_chat(session_id)
            if chat and chat["messages"]:
                for m in reversed(chat["messages"]):
                    if m["role"] == "assistant" and m.get("content"):
                        m["audio_url"] = audio_url
                        break
                chat_manager.save_chat(chat)
        except Exception as exc:  # noqa: BLE001
            audio_url = None
            result["tts_error"] = str(exc)

    chat = chat_manager.load_chat(session_id)

    # Background auto-sync to Notion: create the page on the first turn,
    # append only the newly-added messages on every subsequent turn.
    if chat is not None and os.getenv("NOTION_API_KEY") and os.getenv("NOTION_PARENT_PAGE_ID"):
        try:
            sync_state = {**chat, "session_id": session_id}
            sync_result = _sync_chat_to_notion(sync_state)
            if not sync_result.get("ok"):
                result.setdefault("notion_error", sync_result.get("error"))
            chat = chat_manager.load_chat(session_id)
        except Exception as exc:  # noqa: BLE001
            result.setdefault("notion_error", str(exc))

    return {
        "session_id": session_id,
        "reply": result["text"],
        "audio_url": audio_url,
        "tool_trace": result["tool_trace"],
        "chat": chat,
    }


# ---------------------------------------------------------------------------
# 3) Text-to-speech
# ---------------------------------------------------------------------------
def _tts_to_file(text: str) -> str:
    client = get_openai()
    fname = f"out_{uuid.uuid4().hex}.mp3"
    fpath = AUDIO_DIR / fname
    with client.audio.speech.with_streaming_response.create(
        model=OPENAI_TTS_MODEL,
        voice=OPENAI_TTS_VOICE,
        input=text,
    ) as response:
        response.stream_to_file(fpath)
    return f"/audio/{fname}"


class TTSRequest(BaseModel):
    text: str


@app.post("/api/tts")
def tts(req: TTSRequest) -> dict[str, str]:
    if not req.text.strip():
        raise HTTPException(400, "text is required")
    return {"audio_url": _tts_to_file(req.text)}


# ---------------------------------------------------------------------------
# 4) Feedback (continuous learning)
# ---------------------------------------------------------------------------
class FeedbackRequest(BaseModel):
    session_id: str
    message_index: int
    rating: str  # "good" | "bad"


@app.post("/api/feedback")
def feedback(req: FeedbackRequest) -> dict[str, Any]:
    if req.rating not in {"good", "bad"}:
        raise HTTPException(400, "rating must be 'good' or 'bad'")
    msg = chat_manager.rate_message(req.session_id, req.message_index, req.rating)
    if msg is None:
        raise HTTPException(404, "message not found")
    user_query = None
    chat = chat_manager.load_chat(req.session_id)
    if chat:
        for m in reversed(chat["messages"][: req.message_index]):
            if m["role"] == "user":
                user_query = m["content"]
                break
    out = learning.record_feedback(
        msg.get("content", ""),
        req.rating,
        session_id=req.session_id,
        user_query=user_query,
    )
    return out


@app.get("/api/learning/stats")
def learning_stats() -> dict[str, Any]:
    return learning.stats()


# ---------------------------------------------------------------------------
# 5) Notion sync
# ---------------------------------------------------------------------------
class SyncRequest(BaseModel):
    session_id: str


@app.post("/api/sync_notion")
def sync_notion(req: SyncRequest) -> dict[str, Any]:
    chat = chat_manager.load_chat(req.session_id)
    if chat is None:
        raise HTTPException(404, "session not found")
    sync_state = {**chat, "session_id": req.session_id}
    return _sync_chat_to_notion(sync_state)


@app.post("/api/sync_notion/all")
def sync_notion_all() -> dict[str, Any]:
    """Push every locally-stored chat to Notion (creates pages where missing,
    appends new messages where a page already exists)."""
    if not (os.getenv("NOTION_API_KEY") and os.getenv("NOTION_PARENT_PAGE_ID")):
        raise HTTPException(400, "Notion is not configured (.env)")
    return _backfill_all_chats_to_notion()


def _backfill_all_chats_to_notion() -> dict[str, Any]:
    summary: dict[str, Any] = {"synced": 0, "skipped": 0, "errors": []}
    for entry in chat_manager.list_chats():
        chat = chat_manager.load_chat(entry["id"])
        if chat is None or not chat.get("messages"):
            summary["skipped"] += 1
            continue
        try:
            res = _sync_chat_to_notion({**chat, "session_id": chat["id"]})
            if res.get("ok"):
                summary["synced"] += 1
            else:
                summary["errors"].append({"id": chat["id"], "error": res.get("error")})
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append({"id": chat["id"], "error": str(exc)})
    return summary


def _hydrate_chats_from_notion() -> dict[str, Any]:
    """List every chat page under ``NOTION_PARENT_PAGE_ID`` and create a stub
    (``messages: []``, ``notion_page_id`` set) for any session_id we don't
    already have. In Notion-only mode (the default when Notion is configured)
    these stubs live in memory; otherwise they're written to disk.

    Returns ``{added, already_present, ok, error}``.
    """
    listing = list_notion_chat_pages()
    if not listing.get("ok"):
        return {
            "ok": False,
            "added": 0,
            "already_present": 0,
            "error": listing.get("error"),
        }

    added = already = 0
    for entry in listing["chats"]:
        sid = entry["session_id"]
        if chat_manager.load_chat(sid) is not None:
            already += 1
            continue
        chat_manager.write_chat_from_messages(
            sid,
            entry["title"],
            messages=[],
            notion_page_id=entry["page_id"],
            notion_url=entry["url"],
        )
        # Stubs have no messages yet, so reset synced_count to 0 - the next
        # push will append any messages we collect locally.
        chat_manager.update_notion_state(sid, synced_count=0)
        added += 1
    return {
        "ok": True,
        "added": added,
        "already_present": already,
        "error": None,
    }


@app.on_event("startup")
def _startup_pull_from_notion() -> None:
    """Fresh-clone friendly: on boot, fetch the chat list from this user's
    own Notion parent page and hydrate the in-memory store. Each user only
    ever sees the chats that live under THEIR configured
    ``NOTION_PARENT_PAGE_ID``.

    Message bodies are loaded lazily by ``get_session`` the first time the
    user opens a chat, to keep startup fast.
    """
    mode = "in-memory + Notion" if chat_manager.use_memory_only() else "local files"
    print(f"[chat] storage mode: {mode}")

    if not chat_manager.use_memory_only():
        print("[notion] skipped startup pull: NOTION_API_KEY / NOTION_PARENT_PAGE_ID not set.")
        return
    try:
        result = _hydrate_chats_from_notion()
        if not result["ok"]:
            print(f"[notion] startup pull failed: {result['error']}")
            return
        print(
            f"[notion] startup pull: hydrated {result['added']} chat(s), "
            f"{result['already_present']} already cached."
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[notion] startup pull crashed: {exc}")


# ---------------------------------------------------------------------------
# 6) Sessions  (multi-chat + history)
# ---------------------------------------------------------------------------
@app.get("/api/sessions")
def list_sessions() -> list[dict[str, Any]]:
    return chat_manager.list_chats()


class NewChatRequest(BaseModel):
    title: str | None = None


@app.post("/api/sessions")
def new_session(req: NewChatRequest) -> dict[str, Any]:
    return chat_manager.create_chat(req.title)


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    chat = chat_manager.load_chat(session_id)
    if chat is None:
        raise HTTPException(404, "session not found")

    # Lazy hydration: if this is a Notion-only stub (we know a page id but
    # have no messages locally), pull the messages now. Avoids the cost of
    # fetching every chat at boot.
    is_stub = not chat.get("messages") and chat.get("notion_page_id")
    if is_stub:
        fetched = fetch_notion_page_messages(chat["notion_page_id"])
        if fetched.get("ok"):
            chat = chat_manager.write_chat_from_messages(
                session_id,
                chat.get("title") or "Restored chat",
                fetched.get("messages", []),
                notion_page_id=chat.get("notion_page_id"),
                notion_url=chat.get("notion_url"),
            )
        else:
            chat["notion_fetch_error"] = fetched.get("error")
    return chat


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict[str, Any]:
    """Soft-delete: move the chat JSON to ``data/chats/_deleted/`` so it can
    be restored via ``POST /api/sessions/{id}/restore``. The Notion copy (if
    any) is intentionally left untouched."""
    if not chat_manager.delete_chat(session_id):
        raise HTTPException(404, "session not found")
    return {"ok": True, "session_id": session_id}


@app.post("/api/sessions/{session_id}/restore")
def restore_session(session_id: str) -> dict[str, Any]:
    """Undo a delete. First tries the local soft-delete backup; if there isn't
    one but the chat was previously synced to Notion, rebuilds it from the
    Notion page."""
    restored = chat_manager.restore_chat(session_id)
    if restored is not None:
        return {"ok": True, "source": "local", "chat": restored}

    # Fallback: rehydrate from Notion if we still know the page id. Look in
    # the deleted-trash for the chat metadata we kept.
    page_id: str | None = None
    title: str | None = None
    deleted_meta = chat_manager.get_deleted_metadata(session_id)
    if deleted_meta is not None:
        page_id = deleted_meta.get("notion_page_id")
        title = deleted_meta.get("title")

    if not page_id:
        raise HTTPException(
            404,
            "No local backup and no Notion page id available - this chat "
            "cannot be restored.",
        )

    fetched = fetch_notion_page_messages(page_id)
    if not fetched.get("ok"):
        raise HTTPException(502, f"Notion fetch failed: {fetched.get('error')}")

    chat = chat_manager.write_chat_from_messages(
        session_id,
        title or f"Restored {session_id}",
        fetched.get("messages", []),
        notion_page_id=page_id,
    )
    return {"ok": True, "source": "notion", "chat": chat}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "notion_configured": bool(
            os.getenv("NOTION_API_KEY") and os.getenv("NOTION_PARENT_PAGE_ID")
        ),
        "chat_storage": "memory+notion" if chat_manager.use_memory_only() else "local-file",
        "chat_model": OPENAI_CHAT_MODEL,
        "asr_model": OPENAI_ASR_MODEL,
        "tts_model": OPENAI_TTS_MODEL,
    }


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "Voice_Agent_Chatbot:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )
