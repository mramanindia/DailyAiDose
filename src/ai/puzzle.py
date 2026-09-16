"""Brain teasers for days when the digest has little or nothing to show.

A quiet day should still be worth opening, so the ClickUp message carries a
puzzle instead of an apology. Puzzles are generated fresh by the cheap
analysis model and fall back to a hand written bank in data/puzzles.json
when generation fails or produces something unusable.

Puzzles are for a mixed audience, so anything needing specialist knowledge
is rejected: the point is that anyone in the channel can have a go.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..models import AIConfig
from .client import create_ai_client

BANK_PATH = Path("data/puzzles.json")
STATE_PATH = Path("data/puzzle_state.json")

# A generated puzzle drifts technical over time no matter how the prompt is
# worded, so anything hitting these words goes back to the bank.
_JARGON = {
    "algorithm", "api", "backend", "bandwidth", "benchmark", "cache",
    "cloud", "cluster", "compiler", "cpu", "database", "dataset", "debug",
    "deploy", "encryption", "frontend", "gpu", "inference", "kernel",
    "kubernetes", "latency", "llm", "matrix", "model", "neural", "node",
    "parameter", "pipeline", "prompt", "protocol", "python", "query",
    "repository", "runtime", "server", "software", "token", "transformer",
    "variable",
}
_WORD_RE = re.compile(r"[a-z]+")

_MIN_PROMPT = 20
_MAX_PROMPT = 400
_MAX_ANSWER = 320

_SYSTEM = (
    "You write short brain teasers for a mixed office audience: salespeople, "
    "designers, accountants and engineers all read the same message. "
    "Write for the accountant, not the engineer.\n\n"
    "Rules:\n"
    "- Solvable by anyone with everyday knowledge. No technical, industry or "
    "financial jargon. No specialist vocabulary of any kind.\n"
    "- One single unambiguous answer. No trick that depends on wordplay in a "
    "language other than English.\n"
    "- The fun is in the moment someone realises their first instinct was "
    "wrong, so favour teasers that mislead before they click.\n"
    "- Keep the puzzle under 60 words. Keep the answer under 50 words, and "
    "say briefly why the obvious answer is wrong.\n"
    "- Plain sentences only. No em dashes, no headings, no emoji, no "
    "preamble, no sign off.\n\n"
    'Reply with JSON only: {"prompt": "...", "answer": "..."}'
)

_KINDS = [
    "a word puzzle about the English language",
    "a lateral thinking puzzle with an everyday setting",
    "a small arithmetic puzzle where the obvious answer is wrong",
    "a puzzle about spotting an assumption nobody questions",
    "a logic puzzle about people, objects or days of the week",
    "a counting puzzle with a surprising total",
    "a riddle with a one word answer",
]


@dataclass
class Puzzle:
    """A puzzle and its answer, ready to render into a chat message."""

    prompt: str
    answer: str
    source: str = "bank"


def _looks_usable(prompt: str, answer: str) -> bool:
    """Reject generated puzzles that are empty, bloated or jargon heavy."""
    if not prompt or not answer:
        return False
    if not (_MIN_PROMPT <= len(prompt) <= _MAX_PROMPT):
        return False
    if len(answer) > _MAX_ANSWER:
        return False
    if "```" in prompt or "```" in answer:
        return False
    if "—" in prompt or "—" in answer:
        return False
    words = set(_WORD_RE.findall(f"{prompt} {answer}".lower()))
    return not (words & _JARGON)


def _parse(raw: str) -> Optional[Puzzle]:
    """Pull a puzzle out of a model reply, tolerating fenced JSON."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    prompt = str(data.get("prompt") or "").strip()
    answer = str(data.get("answer") or "").strip()
    if not _looks_usable(prompt, answer):
        return None
    return Puzzle(prompt=prompt, answer=answer, source="generated")


def _load_bank(bank_path: Path) -> List[dict]:
    try:
        data = json.loads(bank_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    puzzles = data.get("puzzles") if isinstance(data, dict) else data
    return puzzles if isinstance(puzzles, list) else []


def _load_state(state_path: Path) -> dict:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state_path: Path, used: List[str], date: str, chosen: str) -> None:
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"used": used, "date": date, "id": chosen}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass  # a puzzle that repeats beats a digest that fails to post


def _to_puzzle(entry: dict) -> Optional[Puzzle]:
    prompt = str(entry.get("prompt") or "").strip()
    answer = str(entry.get("answer") or "").strip()
    if not prompt or not answer:
        return None
    return Puzzle(prompt=prompt, answer=answer, source="bank")


def from_bank(
    date: str,
    bank_path: Path = BANK_PATH,
    state_path: Path = STATE_PATH,
) -> Optional[Puzzle]:
    """Pick an unused puzzle from the bank, cycling once it runs dry.

    Selection is seeded by the date so a rerun of the same day posts the same
    puzzle rather than burning a second one.
    """
    bank = _load_bank(bank_path)
    if not bank:
        return None

    state = _load_state(state_path)
    used = [str(u) for u in state.get("used", []) if isinstance(state.get("used"), list)]

    # The backup cron and manual dispatches can rerun the same day. Repeat that
    # day's puzzle instead of spending another one from the bank.
    if state.get("date") == date:
        repeat = next((p for p in bank if str(p.get("id")) == state.get("id")), None)
        if repeat is not None:
            return _to_puzzle(repeat)

    remaining = [p for p in bank if str(p.get("id")) not in set(used)]
    if not remaining:
        used = []
        remaining = bank

    chosen = random.Random(date).choice(remaining)
    puzzle = _to_puzzle(chosen)
    if puzzle is None:
        return None

    _save_state(state_path, used + [str(chosen.get("id"))], date, str(chosen.get("id")))
    return puzzle


async def generate(date: str, ai_config: AIConfig) -> Optional[Puzzle]:
    """Ask the cheap analysis model for a fresh puzzle. None if unusable."""
    kind = _KINDS[hash(date) % len(_KINDS)]
    config = ai_config.model_copy(
        update={"model": ai_config.analysis_model or ai_config.model}
    )
    try:
        client = create_ai_client(config)
        raw = await client.complete(
            system=_SYSTEM,
            user=f"Write {kind}. Reply with JSON only.",
            temperature=1.0,
            max_tokens=600,
        )
    except Exception:
        return None
    return _parse(raw)


async def get_puzzle(
    date: str,
    ai_config: Optional[AIConfig] = None,
    bank_path: Path = BANK_PATH,
    state_path: Path = STATE_PATH,
) -> Optional[Puzzle]:
    """Return a puzzle for the day, preferring a freshly generated one."""
    if ai_config is not None:
        puzzle = await generate(date, ai_config)
        if puzzle is not None:
            return puzzle
    return from_bank(date, bank_path=bank_path, state_path=state_path)
