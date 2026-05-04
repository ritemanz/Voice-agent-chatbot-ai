// Frontend logic for the voice-driven research assistant.

const els = {
  recordBtn: document.getElementById("record-btn"),
  textInput: document.getElementById("text-input"),
  sendBtn: document.getElementById("send-btn"),
  messages: document.getElementById("messages"),
  chatList: document.getElementById("chat-list"),
  newChatBtn: document.getElementById("new-chat-btn"),
  status: document.getElementById("status"),
  chatTitle: document.getElementById("chat-title"),
  chatMeta: document.getElementById("chat-meta"),
  learningStats: document.getElementById("learning-stats"),
  notionLink: document.getElementById("notion-link"),
};

const state = {
  sessionId: null,
  recorder: null,
  recording: false,
  chunks: [],
};

const setStatus = (s) => (els.status.textContent = s);

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------
async function loadSessions() {
  const res = await fetch("/api/sessions");
  const sessions = await res.json();
  els.chatList.innerHTML = "";

  if (!sessions.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "No past chats yet — start one!";
    els.chatList.appendChild(empty);
    return sessions;
  }

  for (const s of sessions) {
    const li = document.createElement("li");
    if (s.id === state.sessionId) li.classList.add("active");
    const notionMark = s.notion_url ? ' · <span class="meta-notion">Notion</span>' : "";
    li.innerHTML = `
      <div class="row">
        <div class="info">
          <div class="title">${escapeHtml(s.title || "Untitled")}</div>
          <div class="meta">${s.message_count} msgs · ${formatDate(s.updated_at)}${notionMark}</div>
        </div>
        <button class="del-btn" title="Delete this chat (Notion copy is kept)" aria-label="Delete chat">×</button>
      </div>
    `;
    li.addEventListener("click", () => openSession(s.id));
    li.querySelector(".del-btn").addEventListener("click", (ev) => {
      ev.stopPropagation();
      deleteSession(s);
    });
    els.chatList.appendChild(li);
  }
  return sessions;
}

async function deleteSession(s) {
  const wasOpen = state.sessionId === s.id;
  setStatus("deleting…");
  const res = await fetch(`/api/sessions/${s.id}`, { method: "DELETE" });
  if (!res.ok) {
    setStatus("delete failed");
    showToast("Failed to delete chat.", { type: "error" });
    return;
  }
  setStatus("idle");

  if (wasOpen) {
    state.sessionId = null;
    const sessions = await loadSessions();
    if (sessions && sessions.length) {
      await openSession(sessions[0].id);
    } else {
      await newChat();
    }
  } else {
    await loadSessions();
  }

  showToast(`Chat "${s.title || "Untitled"}" deleted`, {
    actionLabel: "Undo",
    timeoutMs: 12000,
    onAction: () => restoreSession(s.id),
  });
}

async function restoreSession(sessionId) {
  setStatus("restoring…");
  const res = await fetch(`/api/sessions/${sessionId}/restore`, { method: "POST" });
  if (!res.ok) {
    setStatus("restore failed");
    let msg = "Could not restore chat.";
    try {
      const data = await res.json();
      if (data.detail) msg += `\n\n${data.detail}`;
    } catch {}
    showToast(msg, { type: "error" });
    return;
  }
  const data = await res.json();
  await loadSessions();
  await openSession(sessionId);
  setStatus("idle");
  showToast(
    data.source === "notion"
      ? "Chat restored from Notion"
      : "Chat restored from local backup"
  );
}

// ---------------------------------------------------------------------------
// Toasts
// ---------------------------------------------------------------------------
let _toastEl = null;
let _toastTimer = null;

function showToast(message, opts = {}) {
  const { actionLabel, onAction, timeoutMs = 4000, type = "info" } = opts;

  if (_toastTimer) {
    clearTimeout(_toastTimer);
    _toastTimer = null;
  }
  if (_toastEl) {
    _toastEl.remove();
    _toastEl = null;
  }

  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  const text = document.createElement("span");
  text.textContent = message;
  el.appendChild(text);

  if (actionLabel && onAction) {
    const btn = document.createElement("button");
    btn.className = "toast-action";
    btn.textContent = actionLabel;
    btn.addEventListener("click", () => {
      hideToast();
      try { onAction(); } catch (e) { console.error(e); }
    });
    el.appendChild(btn);
  }

  const close = document.createElement("button");
  close.className = "toast-close";
  close.textContent = "×";
  close.addEventListener("click", hideToast);
  el.appendChild(close);

  document.body.appendChild(el);
  _toastEl = el;
  _toastTimer = setTimeout(hideToast, timeoutMs);
}

function hideToast() {
  if (_toastTimer) {
    clearTimeout(_toastTimer);
    _toastTimer = null;
  }
  if (_toastEl) {
    _toastEl.classList.add("toast-leaving");
    const el = _toastEl;
    _toastEl = null;
    setTimeout(() => el.remove(), 200);
  }
}

async function openSession(id) {
  const res = await fetch(`/api/sessions/${id}`);
  if (!res.ok) return;
  const chat = await res.json();
  state.sessionId = chat.id;
  els.chatTitle.textContent = chat.title || "Chat";
  els.chatMeta.textContent = `id: ${chat.id} · created ${formatDate(chat.created_at)}`;
  updateNotionLink(chat.notion_url);
  renderMessages(chat.messages || []);
  loadSessions();
}

function updateNotionLink(url) {
  if (!els.notionLink) return;
  if (url) {
    els.notionLink.href = url;
    els.notionLink.hidden = false;
  } else {
    els.notionLink.hidden = true;
    els.notionLink.removeAttribute("href");
  }
}

async function newChat() {
  const res = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  const chat = await res.json();
  state.sessionId = chat.id;
  els.chatTitle.textContent = chat.title || "New chat";
  els.chatMeta.textContent = `id: ${chat.id}`;
  els.messages.innerHTML = "";
  updateNotionLink(null);
  loadSessions();
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function renderMessages(messages) {
  els.messages.innerHTML = "";
  messages.forEach((m, idx) => {
    if (m.role === "system") return;
    const div = document.createElement("div");
    div.className = `msg ${m.role}`;

    const head = document.createElement("div");
    head.className = "role";
    const left = document.createElement("span");
    left.textContent = m.role + (m.name ? ` · ${m.name}` : "");
    const right = document.createElement("span");
    right.textContent = m.timestamp || "";
    head.append(left, right);
    div.appendChild(head);

    if (m.role === "tool") {
      const pre = document.createElement("pre");
      pre.style.margin = 0;
      pre.style.whiteSpace = "pre-wrap";
      pre.textContent = truncate(m.content || "", 1200);
      div.appendChild(pre);
    } else {
      const body = document.createElement("div");
      body.className = "body";
      const text = m.content || (m.tool_calls ? "_(calling tools…)_" : "");
      if (m.role === "assistant") {
        body.innerHTML = renderMarkdown(text);
      } else {
        body.textContent = text;
      }
      div.appendChild(body);
    }

    if (m.audio_url) {
      const audio = document.createElement("audio");
      audio.controls = true;
      audio.src = m.audio_url;
      div.appendChild(audio);
    }

    if (m.role === "assistant" && (m.content || "").trim()) {
      const actions = document.createElement("div");
      actions.className = "actions";

      const goodBtn = document.createElement("button");
      goodBtn.className = "good" + (m.rating === "good" ? " active" : "");
      goodBtn.textContent = "Good";
      goodBtn.onclick = () => rate(idx, "good");

      const badBtn = document.createElement("button");
      badBtn.className = "bad" + (m.rating === "bad" ? " active" : "");
      badBtn.textContent = "Bad";
      badBtn.onclick = () => rate(idx, "bad");

      const playBtn = document.createElement("button");
      playBtn.textContent = m.audio_url ? "Replay" : "Speak";
      playBtn.onclick = () => playOrSynthesize(div, m, idx);

      actions.append(goodBtn, badBtn, playBtn);
      div.appendChild(actions);
    }

    els.messages.appendChild(div);
  });
  els.messages.scrollTop = els.messages.scrollHeight;
}

async function rate(messageIndex, rating) {
  if (!state.sessionId) return;
  setStatus(`recording ${rating} feedback…`);
  await fetch("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: state.sessionId,
      message_index: messageIndex,
      rating,
    }),
  });
  setStatus("idle");
  await openSession(state.sessionId);
  loadLearningStats();
}

async function playOrSynthesize(container, msg, idx) {
  let url = msg.audio_url;
  if (!url) {
    setStatus("synthesizing speech…");
    const res = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: msg.content }),
    });
    const data = await res.json();
    url = data.audio_url;
    setStatus("idle");
  }
  let audio = container.querySelector("audio");
  if (!audio) {
    audio = document.createElement("audio");
    audio.controls = true;
    container.appendChild(audio);
  }
  audio.src = url;
  audio.play().catch(() => {});
}

// ---------------------------------------------------------------------------
// Sending
// ---------------------------------------------------------------------------
async function sendText(text) {
  if (!text.trim()) return;
  if (!state.sessionId) await newChat();
  setStatus("thinking…");
  els.sendBtn.disabled = true;

  // Optimistic render
  const optimistic = [
    ...currentMessagesOnPage(),
    { role: "user", content: text, timestamp: "" },
  ];
  renderMessages(optimistic);
  els.textInput.value = "";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: state.sessionId,
        user_text: text,
        speak: true,
      }),
    });
    const data = await res.json();
    state.sessionId = data.session_id;
    if (data.chat) {
      els.chatTitle.textContent = data.chat.title || "Chat";
      updateNotionLink(data.chat.notion_url);
      renderMessages(data.chat.messages);
    }
    if (data.audio_url) {
      const audios = els.messages.querySelectorAll("audio");
      const last = audios[audios.length - 1];
      if (last) last.play().catch(() => {});
    }
    setStatus("idle");
    loadSessions();
  } catch (e) {
    console.error(e);
    setStatus("error");
  } finally {
    els.sendBtn.disabled = false;
  }
}

function currentMessagesOnPage() {
  return Array.from(els.messages.querySelectorAll(".msg")).map((d) => ({
    role: d.className.replace("msg ", ""),
    content: d.querySelector("div:not(.role):not(.actions)")?.textContent || "",
  }));
}

// ---------------------------------------------------------------------------
// Recording (Whisper ASR)
// ---------------------------------------------------------------------------
async function toggleRecord() {
  if (state.recording) return stopRecord();
  return startRecord();
}

async function startRecord() {
  if (!navigator.mediaDevices?.getUserMedia) {
    alert("Microphone API not available in this browser.");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = MediaRecorder.isTypeSupported("audio/webm")
      ? "audio/webm"
      : "";
    state.recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    state.chunks = [];
    state.recorder.ondataavailable = (e) => {
      if (e.data.size > 0) state.chunks.push(e.data);
    };
    state.recorder.onstop = onRecordingStopped;
    state.recorder.start();
    state.recording = true;
    els.recordBtn.classList.add("recording");
    els.recordBtn.querySelector(".label").textContent = "Stop & transcribe";
    setStatus("recording…");
  } catch (e) {
    console.error(e);
    alert("Could not access microphone: " + e.message);
  }
}

function stopRecord() {
  if (state.recorder && state.recording) {
    state.recorder.stop();
    state.recorder.stream.getTracks().forEach((t) => t.stop());
    state.recording = false;
    els.recordBtn.classList.remove("recording");
    els.recordBtn.querySelector(".label").textContent = "Hold / click to record";
    setStatus("transcribing…");
  }
}

async function onRecordingStopped() {
  const blob = new Blob(state.chunks, { type: "audio/webm" });
  const fd = new FormData();
  fd.append("audio", blob, "recording.webm");
  try {
    const res = await fetch("/api/transcribe", { method: "POST", body: fd });
    const raw = await res.text();
    let data = {};
    try { data = JSON.parse(raw); } catch { /* not JSON */ }

    if (!res.ok) {
      const msg = data.detail || data.error || raw || `HTTP ${res.status}`;
      console.error("transcribe failed:", msg);
      setStatus("transcribe error");
      alert("Transcription failed:\n\n" + msg);
      return;
    }

    if (data.text) {
      els.textInput.value = data.text;
      await sendText(data.text);
    } else {
      setStatus("no speech detected");
    }
  } catch (e) {
    console.error(e);
    setStatus("transcribe error");
    alert("Transcription error: " + e.message);
  }
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------
async function loadLearningStats() {
  try {
    const res = await fetch("/api/learning/stats");
    const s = await res.json();
    els.learningStats.textContent =
      `Learning memory: +${s.good_total} / -${s.bad_total} · ${s.history_size} samples`;
  } catch (e) {
    /* ignore */
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// Tiny, dependency-free Markdown renderer.
// Handles: code blocks, inline code, bold, italic, links, headings, lists, line breaks.
// Always escapes HTML first, so it's XSS-safe.
function renderMarkdown(src) {
  if (!src) return "";
  let text = String(src);

  // Pull out fenced code blocks first so their contents don't get mangled.
  const codeBlocks = [];
  text = text.replace(/```([\w-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    codeBlocks.push({ lang, code });
    return `\u0000CODEBLOCK${codeBlocks.length - 1}\u0000`;
  });

  // Pull out inline code too.
  const inlineCodes = [];
  text = text.replace(/`([^`\n]+)`/g, (_, code) => {
    inlineCodes.push(code);
    return `\u0000INLINECODE${inlineCodes.length - 1}\u0000`;
  });

  // Now it's safe to escape everything else.
  text = escapeHtml(text);

  // Headings (#, ##, ###).
  text = text.replace(/^###\s+(.+)$/gm, "<h4>$1</h4>");
  text = text.replace(/^##\s+(.+)$/gm, "<h3>$1</h3>");
  text = text.replace(/^#\s+(.+)$/gm, "<h2>$1</h2>");

  // Bold: **text** or __text__
  text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");

  // Italic: *text* or _text_  (avoid matching ** which is already replaced)
  text = text.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  text = text.replace(/(^|[^_])_([^_\n]+)_(?!_)/g, "$1<em>$2</em>");

  // Links: [label](url)
  text = text.replace(
    /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
  );

  // Bare URLs.
  text = text.replace(
    /(^|[\s(])(https?:\/\/[^\s)<]+)/g,
    '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>'
  );

  // Lists: collect consecutive lines starting with - or * or 1. into <ul>/<ol>.
  text = wrapLists(text);

  // Paragraph / line breaks: turn double newlines into paragraph splits and
  // single newlines into <br>.
  text = text
    .split(/\n{2,}/)
    .map((block) => {
      if (/^\s*<(h\d|ul|ol|pre|blockquote|table)/.test(block)) return block;
      return "<p>" + block.replace(/\n/g, "<br>") + "</p>";
    })
    .join("\n");

  // Restore inline code.
  text = text.replace(/\u0000INLINECODE(\d+)\u0000/g, (_, i) =>
    `<code>${escapeHtml(inlineCodes[+i])}</code>`
  );

  // Restore code blocks.
  text = text.replace(/\u0000CODEBLOCK(\d+)\u0000/g, (_, i) => {
    const { lang, code } = codeBlocks[+i];
    const langAttr = lang ? ` class="lang-${escapeHtml(lang)}"` : "";
    return `<pre><code${langAttr}>${escapeHtml(code.replace(/\n$/, ""))}</code></pre>`;
  });

  return text;
}

function wrapLists(text) {
  const lines = text.split("\n");
  const out = [];
  let buf = [];
  let listType = null; // "ul" | "ol"

  const flush = () => {
    if (!listType) return;
    out.push(`<${listType}>${buf.map((l) => `<li>${l}</li>`).join("")}</${listType}>`);
    buf = [];
    listType = null;
  };

  for (const line of lines) {
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ul) {
      if (listType !== "ul") flush();
      listType = "ul";
      buf.push(ul[1]);
    } else if (ol) {
      if (listType !== "ol") flush();
      listType = "ol";
      buf.push(ol[1]);
    } else {
      flush();
      out.push(line);
    }
  }
  flush();
  return out.join("\n");
}
function formatDate(s) {
  if (!s) return "";
  try { return new Date(s).toLocaleString(); } catch { return s; }
}
function truncate(s, n) {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

// ---------------------------------------------------------------------------
// Wire up
// ---------------------------------------------------------------------------
els.recordBtn.addEventListener("click", toggleRecord);
els.sendBtn.addEventListener("click", () => sendText(els.textInput.value));
els.textInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendText(els.textInput.value);
  }
});
els.newChatBtn.addEventListener("click", newChat);

async function bootstrap() {
  setStatus("loading chats…");
  let sessions = [];
  try {
    sessions = await loadSessions();
  } catch (e) {
    console.error("Could not load past chats:", e);
    setStatus("error loading chats");
  }

  if (sessions && sessions.length) {
    // Auto-open the most recently updated chat (already sorted by server).
    await openSession(sessions[0].id);
    setStatus(`loaded ${sessions.length} past chat${sessions.length === 1 ? "" : "s"}`);
  } else {
    // No saved chats yet → start a fresh one.
    await newChat();
    setStatus("idle");
  }
  loadLearningStats();
}

bootstrap();
