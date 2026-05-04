"""
Lightweight "continuous learning" through feedback-driven feature memory.

We can't fine-tune GPT in real time, but we *can* simulate continuous
learning by:

1. Extracting structural + lexical features from every assistant response.
2. When the user marks a response good, those features get a positive vote.
3. When they mark it bad, those features get a negative vote.
4. The feature memory is then injected into the system prompt of the next
   request so the model emulates the patterns of good responses and avoids
   the patterns of bad ones.

State is persisted to a JSON file so the model "improves" across runs.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"
FEEDBACK_FILE = DATA_DIR / "feedback.json"

# Feature catalog - simple, interpretable, easy to tune.
_STRUCTURAL_FEATURES: dict[str, callable] = {
    "uses_bullet_points": lambda t: bool(re.search(r"^\s*[-*]\s+", t, re.M)),
    "uses_numbered_list": lambda t: bool(re.search(r"^\s*\d+\.\s+", t, re.M)),
    "uses_headings": lambda t: bool(re.search(r"^#+\s+", t, re.M)),
    "cites_sources": lambda t: ("arxiv" in t.lower())
    or bool(re.search(r"\[[^\]]+\]\(https?://", t)),
    "includes_code_block": lambda t: "```" in t,
    "is_concise": lambda t: len(t) < 600,
    "is_detailed": lambda t: len(t) > 1500,
    "asks_clarifying_question": lambda t: t.strip().endswith("?"),
    "uses_examples": lambda t: "example" in t.lower() or "e.g." in t.lower(),
    "uses_step_by_step": lambda t: bool(
        re.search(r"\b(step\s*\d+|first,|second,|finally,)\b", t, re.I)
    ),
}


def _ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not FEEDBACK_FILE.exists():
        FEEDBACK_FILE.write_text(
            json.dumps(
                {"good": {}, "bad": {}, "history": []},
                indent=2,
            ),
            encoding="utf-8",
        )


def _load() -> dict[str, Any]:
    _ensure_storage()
    return json.loads(FEEDBACK_FILE.read_text(encoding="utf-8"))


def _save(state: dict[str, Any]) -> None:
    FEEDBACK_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]{2,}")


def extract_features(text: str) -> list[str]:
    """Return a list of feature tokens describing this response."""
    feats: list[str] = []
    for name, fn in _STRUCTURAL_FEATURES.items():
        try:
            if fn(text):
                feats.append(f"struct::{name}")
        except Exception:  # noqa: BLE001
            continue

    # Top content keywords (very small lexical fingerprint)
    words = [w.lower() for w in _WORD_RE.findall(text)]
    stop = {
        "the", "and", "for", "that", "this", "with", "from", "into", "you",
        "are", "was", "were", "have", "has", "had", "but", "not", "your",
        "they", "their", "there", "what", "which", "when", "where", "will",
        "would", "could", "should", "about", "also", "such", "these", "those",
        "been", "being", "more", "most", "than", "then", "them", "very",
    }
    counts = Counter(w for w in words if w not in stop and len(w) > 3)
    for word, _ in counts.most_common(8):
        feats.append(f"kw::{word}")
    return feats


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def record_feedback(
    response_text: str,
    rating: str,
    session_id: str | None = None,
    user_query: str | None = None,
) -> dict[str, Any]:
    """Record a 'good' or 'bad' rating for a response."""
    if rating not in {"good", "bad"}:
        raise ValueError("rating must be 'good' or 'bad'")

    feats = extract_features(response_text)
    state = _load()
    bucket = state[rating]
    for f in feats:
        bucket[f] = bucket.get(f, 0) + 1

    state["history"].append(
        {
            "session_id": session_id,
            "rating": rating,
            "features": feats,
            "user_query": user_query,
            "response_preview": response_text[:300],
        }
    )
    # Keep history bounded.
    state["history"] = state["history"][-500:]
    _save(state)
    return {
        "recorded": True,
        "rating": rating,
        "features": feats,
        "totals": {"good": sum(state["good"].values()), "bad": sum(state["bad"].values())},
    }


def feature_score(feature: str, state: dict[str, Any]) -> float:
    """Score = log-odds of being in the 'good' bucket vs 'bad'."""
    g = state["good"].get(feature, 0)
    b = state["bad"].get(feature, 0)
    return math.log((g + 1) / (b + 1))


def build_guidance() -> str:
    """Produce a system-prompt addendum reflecting accumulated feedback.

    Promotes top positive features, discourages top negative ones.
    """
    state = _load()
    if not state["good"] and not state["bad"]:
        return ""

    all_feats = set(state["good"]) | set(state["bad"])
    scored = sorted(
        ((f, feature_score(f, state)) for f in all_feats),
        key=lambda x: x[1],
        reverse=True,
    )
    promote = [f for f, s in scored if s > 0.3 and f.startswith("struct::")][:5]
    avoid = [f for f, s in scored if s < -0.3 and f.startswith("struct::")][:5]
    promote_kw = [f.split("::", 1)[1] for f, s in scored if s > 0.3 and f.startswith("kw::")][:8]

    lines = ["## Learned response style (from prior user feedback)"]
    if promote:
        lines.append(
            "Prefer these traits (users rated them GOOD): "
            + ", ".join(p.split("::", 1)[1].replace("_", " ") for p in promote)
            + "."
        )
    if avoid:
        lines.append(
            "Avoid these traits (users rated them BAD): "
            + ", ".join(a.split("::", 1)[1].replace("_", " ") for a in avoid)
            + "."
        )
    if promote_kw:
        lines.append(
            "Topical keywords that have correlated with good responses: "
            + ", ".join(promote_kw)
            + "."
        )
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def stats() -> dict[str, Any]:
    state = _load()
    return {
        "good_total": sum(state["good"].values()),
        "bad_total": sum(state["bad"].values()),
        "history_size": len(state["history"]),
        "top_good": sorted(state["good"].items(), key=lambda x: -x[1])[:10],
        "top_bad": sorted(state["bad"].items(), key=lambda x: -x[1])[:10],
    }
