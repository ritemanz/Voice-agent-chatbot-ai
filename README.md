# Lecture 9 Practice — Voice-Driven Research Assistant

A small but complete voice + RAG agent that puts together every piece from
the lecture:

| # | Feature | Where |
|---|---------|-------|
| 1 | Click-to-record + Whisper ASR transcription | `static/script.js`, `POST /api/transcribe` |
| 2 | `search_arxiv(query)` returning the top 3 papers | `tools.py` |
| 3 | OpenAI **function-calling** orchestrating the tools | `Lecture_9_Practice.py` → `_run_chat_with_tools` |
| 4 | Continuous learning via good/bad feedback memory | `learning.py` |
| 5 | `sync_to_notion(session_id, content)` | `tools.py` |
| 6 | Text-to-speech response with a play button | `POST /api/tts` + frontend |
| 7 | "New chat" + saved past chats / past queries | `chat_manager.py`, sidebar in UI |

---

## 1. Install

```bash
# from inside Lecture_9_Practice_env (the venv directory)
.\Scripts\activate          # Windows PowerShell
pip install -r requirements.txt
```

## 2. Configure

```bash
copy .env.example .env       # Windows
# then edit .env and fill in OPENAI_API_KEY, NOTION_API_KEY, NOTION_PARENT_PAGE_ID
```

For Notion: create an internal integration at
<https://www.notion.so/my-integrations>, copy its secret into `NOTION_API_KEY`,
then open a Notion page, click **Share → Add connections**, pick your
integration, and put that page's ID (the 32-char string in its URL) into
`NOTION_PARENT_PAGE_ID`.

## 3. Run

```bash
python Lecture_9_Practice.py
```

Open <http://localhost:8000>.

> Browsers only allow microphone access on `localhost` or HTTPS. Running on
> `127.0.0.1` / `localhost` is fine.

---

## How each piece works

### Voice in (Whisper ASR)
Click the red **record** button. The browser captures audio via
`MediaRecorder`, posts the blob to `/api/transcribe`, the server forwards it
to OpenAI's `whisper-1`, and the recognised text is sent straight into the
chat pipeline.

### Function-calling orchestration
`tools.py` exposes two tools to GPT (`search_arxiv`, `sync_to_notion`).
`_run_chat_with_tools` runs a multi-turn loop: GPT decides whether to call a
tool, the server runs it, the result is fed back as a `tool` message, and
the loop continues until GPT produces a final answer.

### Continuous learning
Every assistant response is fingerprinted into structural + lexical features
(uses bullets? cites sources? key topical words?). Clicking **Good** /
**Bad** votes those features up or down and writes the deltas to
`data/feedback.json`. Each new request rebuilds a *learned style guide*
(`learning.build_guidance()`) and prepends it to the system prompt — so the
model genuinely shifts its style based on accumulated feedback, across
sessions and runs.

### Notion sync
The **Sync to Notion** button (or GPT itself, via the
`sync_to_notion` tool call) creates a new sub-page under
`NOTION_PARENT_PAGE_ID` containing the full dialogue (user / assistant
messages with timestamps).

### Voice out (TTS)
After every reply, the server hits OpenAI TTS and saves the MP3 under
`/audio/`. The UI auto-plays the latest reply and shows a **Replay** button
on every assistant message.

### Multi-chat + history
Every chat is a JSON file under `data/chats/<id>.json`. The left sidebar
lists them all sorted by recency. Click a chat to load it; click **+ New**
to start a fresh one. All your prior queries and answers (with their
ratings and audio) are right there.

---

## Project layout

```
Lecture_9_Practice_env/
├── Lecture_9_Practice.py     FastAPI server + entry point
├── tools.py                  search_arxiv + sync_to_notion + tool schemas
├── learning.py               feature-based feedback memory
├── chat_manager.py           multi-chat persistence
├── requirements.txt
├── .env.example
├── static/
│   ├── index.html
│   ├── style.css
│   └── script.js
└── data/                     created on first run
    ├── chats/                one JSON per chat session
    ├── audio/                TTS / ASR temp + cached audio
    └── feedback.json         continuous-learning memory
```
"# Voice-agent-chatbot-ai" 
"# Voice-agent-chatbot-ai" 
"# Voice-agent-chatbot-ai" 
