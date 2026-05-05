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

You need **Python 3.11+** on your `PATH`. Clone the repo, then from the
project root run:

```bash
python bootstrap.py
```

That single command creates a local virtual environment at `.venv/`,
upgrades `pip` inside it, and installs everything from `requirements.txt`.
It is idempotent — re-running it just reuses the existing `.venv/`.

Then activate the venv:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

> Why a bootstrap script? `pyvenv.cfg`, `Lib/`, and `Scripts/` are
> machine-specific (they hard-code paths to the Python install that created
> them), so they are intentionally **not** committed. Each cloner generates
> their own venv with paths that match their own machine.

## 2. Configure your API keys

The app reads its credentials from a local `.env` file. **You must add your
own keys before the app will work** — the repo ships with placeholder values
only.

### 2.1 Create your `.env`

```powershell
# Windows PowerShell
copy .env.example .env
```

```bash
# macOS / Linux
cp .env.example .env
```

Then open `.env` in your editor and replace the placeholder values for the
three required variables below.

### 2.2 `OPENAI_API_KEY` (required)

Used for Whisper ASR, chat completion (function-calling), and TTS.

1. Go to <https://platform.openai.com/api-keys>.
2. Click **Create new secret key**, copy the value (it starts with `sk-...`).
3. Paste it into `.env`:

   ```
   OPENAI_API_KEY=sk-...your-real-key...
   ```

### 2.3 `NOTION_API_KEY` (required for Notion sync)

This is the secret of a Notion **internal integration** — it lets the app
create pages in your workspace.

1. Go to <https://www.notion.so/my-integrations>.
2. Click **+ New integration**, give it a name (e.g. "Voice Research
   Assistant"), pick the workspace, and submit.
3. Copy the **Internal Integration Secret** (starts with `ntn_...` /
   `secret_...`) and paste it into `.env`:

   ```
   NOTION_API_KEY=ntn_...your-real-secret...
   ```

### 2.4 `NOTION_PARENT_PAGE_ID` (required for Notion sync)

This is the Notion page that will become the parent of every chat page the
app creates.

1. In Notion, create or pick the page where you want chats to live (e.g. a
   page called "AI Chats").
2. On that page, click **Share → Add connections** and select the integration
   you created above. **Without this step the API will return
   `object_not_found`.**
3. Copy the page's URL. The ID is the 32-character hex string at the end —
   for example, in
   `https://www.notion.so/My-Page-3538e7ab859e80bfa945ecc07e04512d`,
   the ID is `3538e7ab859e80bfa945ecc07e04512d`. Dashes are optional.
4. Paste it into `.env`:

   ```
   NOTION_PARENT_PAGE_ID=3538e7ab859e80bfa945ecc07e04512d
   ```

> **Note**: `.env` is in `.gitignore` and will not be committed. Don't paste
> real secrets into `.env.example`.

### 2.5 Verify

After starting the server, hit <http://localhost:8000/api/health> — you
should see:

```json
{ "ok": true, "openai_configured": true, "notion_configured": true, ... }
```

If `notion_configured` is `false`, double-check `NOTION_API_KEY` and
`NOTION_PARENT_PAGE_ID` in your `.env`.

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
