# Voice Agent Chatbot

A voice-driven research assistant that ties together speech recognition,
function-calling LLMs, an arXiv search tool, text-to-speech playback, and
**Notion-backed chat history** — every conversation is automatically saved
as a sub-page in your own Notion workspace, so your history follows you
across machines.


| #   | Feature                                                                     | Where                                             |
| --- | --------------------------------------------------------------------------- | ------------------------------------------------- |
| 1   | Click-to-record + Whisper ASR transcription                                 | `static/script.js`, `POST /api/transcribe`        |
| 2   | `search_arxiv(query)` returning the top relevant papers                     | `tools.py`                                        |
| 3   | OpenAI **function-calling** orchestrating the tools                         | `Voice_Agent_Chatbot.py` → `_run_chat_with_tools` |
| 4   | Continuous learning via good/bad feedback memory                            | `learning.py`                                     |
| 5   | Auto-sync of every chat to Notion (create page on first turn, append after) | `tools.py` → `_sync_chat_to_notion`               |
| 6   | Text-to-speech response with a play button                                  | `POST /api/tts` + frontend                        |
| 7   | "New chat" + saved past chats / past queries (Notion-backed)                | `chat_manager.py`, sidebar in UI                  |


---

## Quick start (TL;DR)

```bash
# 1. Clone
git clone https://github.com/ritemanz/Voice-agent-chatbot-ai.git
cd Voice-agent-chatbot-ai

# 2. Bootstrap (creates .venv/, installs deps)
python bootstrap.py

# 3. Activate the venv
.\.venv\Scripts\Activate.ps1            # Windows PowerShell
.\.venv\Scripts\Activate.bat            # Windows Command Prompt
# source .venv/bin/activate             # macOS / Linux

# 4. Configure
# Edit .env and fill in OPENAI_API_KEY, NOTION_API_KEY, NOTION_PARENT_PAGE_ID

# OPENAI API KEY:
# https://platform.openai.com/api-keys                 # You may need to add a payment method

# Notion API KEY:
# Create a new Workspace on Notion.
# https://www.notion.so/my-integrations  # 1. Press "Create A New Connection"
                                         # 2. Create a name, choose the newly made workspace 
                                         #    in "Installable in *"
                                         # 3. On the workspace, create a new page that you 
                                         #    can write notes on
                                         # 4. On the Top right corner, click the triple dots
                                         #    and click "Connections" --> "Add New Connection"
                                         #    --> "Name_of_New_Workspace"

# Notion Parent Page ID
# On the top of the new page that was created, there is a link.
    # ex. https://www.notion.so/AI-Voice-Agent-Assistant-3538e7ab859e80bfa07e04512d
# Copy the code after the final -
    # ex. 3538e7ab859e80bfa07e04512d  
# This is the Parent Page ID
                        
# 5. Run
python Voice_Agent_Chatbot.py

# 6. Open http://localhost:8000
```

The full step-by-step is below.

---

## 1. Prerequisites

- **Python 3.11 or later** on your `PATH` (`python --version` should print
`3.11.x` or higher).
- A **microphone** if you want to use voice input (the UI also accepts
typed input, so this is optional).
- An **OpenAI account** with an API key (used for chat, Whisper ASR, and
TTS).
- A **Notion account** (free is fine) — used as the persistent store for
every chat. The app will run without Notion configured, but it will fall
back to local JSON files in that mode.

## 2. Clone the repo

```bash
git clone https://github.com/ritemanz/Voice-agent-chatbot-ai.git
cd Voice-agent-chatbot-ai
```

## 3. Bootstrap (create the venv + install dependencies)

From the project root:

```bash
python bootstrap.py
```

This single command:

1. Creates a virtual environment at `.venv/` (skips if it already exists).
2. Upgrades `pip` inside that venv.
3. Installs every dependency listed in `requirements.txt`
  (`fastapi`, `openai`, `arxiv`, `notion-client`, etc.).

> **Why a bootstrap script?** A Python venv's `pyvenv.cfg`, `Lib/`, and
> `Scripts/` are tied to the absolute path of the Python install that
> created them. They are intentionally **not** committed to the repo, so
> each cloner generates their own venv with paths that match their own
> machine.

## 4. Activate the venv

Every shell session that runs the app needs the venv activated:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```cmd
:: Windows cmd.exe
.venv\Scripts\activate.bat
```

```bash
# macOS / Linux
source .venv/bin/activate
```

You should see `(.venv)` at the start of your prompt afterwards.

## 5. Configure your API keys

The app reads its credentials from a local `.env` file. 

**You must add your own keys before the app will work**

### 5.1 `OPENAI_API_KEY` (required)

Used for Whisper ASR, chat completion (function-calling), and TTS.

1. Go to [https://platform.openai.com/api-keys](https://platform.openai.com/api-keys).
2. Click **Create new secret key**, copy the value (it starts with
  `sk-...`).
3. Paste it into `.env`:
  ```
   OPENAI_API_KEY=sk-...your-real-key...
  ```

### 5.2 `NOTION_API_KEY` (required for chat persistence)

This is the secret of a Notion **internal integration** — it lets the app
create pages in your workspace.

1. Go to [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations).
2. Click **+ New integration**, give it a name (e.g. "Voice Agent
  Chatbot"), pick the workspace, and submit.
3. Copy the **Internal Integration Secret** (starts with `ntn_...` /
  `secret_...`) and paste it into `.env`:

### 5.3 `NOTION_PARENT_PAGE_ID` (required for chat persistence)

This is the Notion page that will become the parent of every chat page
the app creates.

1. In Notion, create or pick the page where you want chats to live (e.g.
  a page called "AI Chats").
2. On that page, click **Share → Add connections** and select the
  integration you created above. **Without this step the API will return
   `object_not_found`** when the app tries to write.
3. Copy the page's URL. The ID is the 32-character hex string at the end
  — for example, in
   `https://www.notion.so/My-Page-3538e7ab859e80bfa945ecc07e04512d`,
   the ID is `3538e7ab859e80bfa945ecc07e04512d`. Dashes are optional.
4. Paste it into `.env`:
  ```
   NOTION_PARENT_PAGE_ID=3538e7ab859e80bfa945ecc07e04512d
  ```

> **Note:** `.env` is listed in `.gitignore` and will not be committed.
> Never paste real secrets into `.env.example`.

## 6. Run the server

```bash
python Voice_Agent_Chatbot.py
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

> Browsers only allow microphone access on `localhost` or HTTPS. Running
> on `127.0.0.1` / `localhost` is fine.

On the first launch the console will print something like:

```
[chat] storage mode: in-memory + Notion
[notion] startup pull: hydrated 0 chat(s), 0 already cached.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

If you've used the app before, `hydrated N chat(s)` shows how many
existing conversations were loaded from your Notion parent page.

## 7. Verify the configuration

In another terminal (or your browser), hit:

```
http://localhost:8000/api/health
```

You should see:

```json
{
  "ok": true,
  "openai_configured": true,
  "notion_configured": true,
  "chat_storage": "memory+notion",
  "chat_model": "gpt-4o-mini",
  "asr_model": "whisper-1",
  "tts_model": "gpt-4o-mini-tts"
}
```

- `openai_configured: false` → check `OPENAI_API_KEY` in `.env`.
- `notion_configured: false` → check `NOTION_API_KEY` and
`NOTION_PARENT_PAGE_ID`.
- `chat_storage: "local-file"` → Notion isn't configured, the app is
using local JSON files as a fallback.

---

## How each piece works

### Voice in (Whisper ASR)

Click the red **record** button. The browser captures audio via
`MediaRecorder`, posts the blob to `/api/transcribe`, the server forwards
it to OpenAI's `whisper-1`, and the recognised text is sent straight
into the chat pipeline.

### Function-calling orchestration

`tools.py` exposes two tools to GPT (`search_arxiv`, `sync_to_notion`).
`_run_chat_with_tools` runs a multi-turn loop: GPT decides whether to
call a tool, the server runs it, the result is fed back as a `tool`
message, and the loop continues until GPT produces a final answer.

### Continuous learning

Every assistant response is fingerprinted into structural + lexical
features (uses bullets? cites sources? key topical words?). Clicking
**Good** / **Bad** votes those features up or down and writes the deltas
to `data/feedback.json`. Each new request rebuilds a *learned style
guide* (`learning.build_guidance()`) and prepends it to the system prompt
— so the model genuinely shifts its style based on accumulated feedback,
across sessions and runs.

### Notion-backed chat storage (no local chat files)

When `NOTION_API_KEY` and `NOTION_PARENT_PAGE_ID` are both set, the app
runs in **in-memory + Notion** mode:

- On boot, the app lists every direct child page of your parent page
whose title matches the `… (<session_id>)` pattern, and hydrates an
in-memory cache with stubs (titles + page IDs only).
- When you click a chat in the sidebar, its messages are lazy-fetched
from Notion the first time and cached in memory.
- After every assistant reply, the chat is auto-pushed to Notion: a new
sub-page on the first turn, append-only on every subsequent turn (no
duplicate pages).
- Deleting a chat in the UI is a soft-delete (held in memory for the
Undo toast); your Notion page is intentionally left untouched.
- **Nothing about chat content is written to disk.** A fresh clone with
an empty Notion parent starts with an empty sidebar and stays that way
until you have your first conversation.

If Notion isn't configured, the app falls back to writing
`data/chats/<id>.json` files exactly like a normal local app.

### Voice out (TTS)

After every reply, the server hits OpenAI TTS and saves the MP3 under
`data/audio/`. The UI auto-plays the latest reply and shows a **Replay**
button on every assistant message. Audio files are local cache only;
they are gitignored and can be deleted at any time.

### Multi-chat + history

The left sidebar lists every chat under your Notion parent page, sorted
by recency. Click a chat to load it; click **+ New** to start a fresh
one; click the **×** on a row to delete (with an Undo toast).

---

## Project layout

```
Voice-agent-chatbot-ai/
├── Voice_Agent_Chatbot.py    FastAPI server + entry point
├── tools.py                  search_arxiv + Notion sync + tool schemas
├── learning.py               feature-based feedback memory
├── chat_manager.py           in-memory + Notion / local-file backends
├── bootstrap.py              one-shot venv + dependency setup
├── requirements.txt
├── .env.example              template - copy to .env and fill in keys
├── .gitignore
├── static/
│   ├── index.html
│   ├── style.css
│   └── script.js
└── data/                     created at runtime, gitignored
    ├── audio/                TTS playback cache (regenerable)
    └── feedback.json         continuous-learning memory
```

---

## Troubleshooting

- `**AuthenticationError: Incorrect API key provided: your-ope...here**` —
you forgot to fill in `OPENAI_API_KEY` in `.env`. Edit it, then restart
the server.
- `**object_not_found` from Notion** — your integration doesn't have
access to the parent page. In Notion, open the page → **Share → Add
connections** → pick your integration.
- **Sidebar is empty even though I have chats in Notion** — make sure
the chat pages are direct children of `NOTION_PARENT_PAGE_ID`, and that
their titles still end in `(<session_id>)` (the app uses that suffix
to identify pages it created).
- **Microphone button does nothing** — browsers only grant `getUserMedia`
on `localhost` or HTTPS. Don't access via the LAN IP.
- **Need to start fresh** — stop the server and delete `data/audio/`* and
`data/feedback.json`. Chats live in Notion, so delete them there if
you want them gone.

