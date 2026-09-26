"""Cross-task lessons (plan §12.2): structured facts extracted at the end
of a run, stored as JSONL -- per-repo at <repo_root>/.raven/memory/lessons.jsonl
(gitignored, like the rest of .raven/) or globally at
<raven-package-root>/memory/global_lessons.jsonl (shipped with the harness,
tracked in git). Deduplicated by simple Jaccard word-overlap on
trigger+insight text (a real BM25 index is unnecessary at this scale) and
retrieved the same way -- deterministic, no model call."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GLOBAL_LESSONS_PATH = REPO_ROOT / "memory" / "global_lessons.jsonl"

DUPLICATE_SIMILARITY_THRESHOLD = 0.6


@dataclass
class Lesson:
    trigger: str
    insight: str
    action: str = ""
    scope: str = "repo"  # "repo" | "global"
    confidence: float = 0.5
    confirmations: int = 1

    def query_text(self) -> str:
        return f"{self.trigger} {self.insight}"


def repo_lessons_path(repo_root: Path) -> Path:
    return Path(repo_root) / ".raven" / "memory" / "lessons.jsonl"


def _words(text: str) -> set[str]:
    return {w.lower() for w in text.split() if len(w) > 2}


def _similarity(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lessons = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            lessons.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return lessons


def _save(path: Path, lessons: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(lesson) for lesson in lessons)
    path.write_text(body + "\n" if lessons else "")


def add_lesson(store_path: Path, lesson: Lesson) -> bool:
    """Appends a new lesson, or increments `confirmations` on an existing
    similar one instead of duplicating it. Returns True if a genuinely new
    lesson was added, False if an existing one was confirmed."""
    lessons = _load(store_path)
    query = lesson.query_text()
    for existing in lessons:
        existing_query = f"{existing.get('trigger', '')} {existing.get('insight', '')}"
        if _similarity(query, existing_query) >= DUPLICATE_SIMILARITY_THRESHOLD:
            existing["confirmations"] = existing.get("confirmations", 1) + 1
            _save(store_path, lessons)
            return False
    lessons.append(asdict(lesson))
    _save(store_path, lessons)
    return True


def _rank(lessons: list[dict], query_text: str, k: int) -> list[dict]:
    scored = [(_similarity(query_text, f"{l.get('trigger', '')} {l.get('insight', '')}"), l) for l in lessons]
    scored = [(score, l) for score, l in scored if score > 0]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [l for _, l in scored[:k]]


def list_lessons(store_path: Path) -> list[dict]:
    """All stored lessons at `store_path`, unranked -- for display (e.g.
    the /lessons slash command), not retrieval (use retrieve_* for that)."""
    return _load(store_path)


def retrieve_lessons(store_path: Path, query_text: str, k: int = 3) -> list[dict]:
    return _rank(_load(store_path), query_text, k)


def retrieve_combined(repo_root: Path, query_text: str, k: int = 3) -> list[dict]:
    """Top-k across repo-scope and global-scope lessons combined, re-ranked
    together by similarity to query_text."""
    all_lessons = _load(repo_lessons_path(repo_root)) + _load(GLOBAL_LESSONS_PATH)
    return _rank(all_lessons, query_text, k)


def format_lesson(lesson: dict) -> str:
    action = f" -> {lesson['action']}" if lesson.get("action") else ""
    return f"{lesson.get('insight', '')}{action}"
