import asyncio
import json
from pathlib import Path

from src.ai import puzzle as puzzle_mod

BANK = {
    "puzzles": [
        {"id": "a", "prompt": "First puzzle, long enough to pass.", "answer": "A"},
        {"id": "b", "prompt": "Second puzzle, long enough to pass.", "answer": "B"},
        {"id": "c", "prompt": "Third puzzle, long enough to pass.", "answer": "C"},
    ]
}


def _bank(tmp_path: Path) -> Path:
    path = tmp_path / "puzzles.json"
    path.write_text(json.dumps(BANK), encoding="utf-8")
    return path


def test_bank_puzzle_is_stable_for_a_given_day(tmp_path: Path) -> None:
    bank, state = _bank(tmp_path), tmp_path / "state.json"

    first = puzzle_mod.from_bank("2026-09-16", bank, state)
    second = puzzle_mod.from_bank("2026-09-16", bank, state)

    # Rerunning the same day repeats the puzzle rather than burning a new one
    assert first.prompt == second.prompt
    assert first.source == "bank"


def test_bank_cycles_without_repeating_until_exhausted(tmp_path: Path) -> None:
    bank, state = _bank(tmp_path), tmp_path / "state.json"

    picks = [
        puzzle_mod.from_bank(f"2026-09-{day:02d}", bank, state).prompt
        for day in (16, 17, 18)
    ]

    assert len(set(picks)) == 3
    assert sorted(json.loads(state.read_text())["used"]) == ["a", "b", "c"]
    # A fourth day resets the bank rather than returning nothing
    assert puzzle_mod.from_bank("2026-09-19", bank, state) is not None


def test_missing_bank_returns_nothing(tmp_path: Path) -> None:
    assert puzzle_mod.from_bank("2026-09-16", tmp_path / "gone.json") is None


def test_generated_json_is_accepted_and_marked_generated() -> None:
    raw = json.dumps(
        {
            "prompt": "A rope ladder hangs over a ship. How many rungs get wet?",
            "answer": "Still three, the ship floats up with the tide.",
        }
    )
    parsed = puzzle_mod._parse(raw)

    assert parsed is not None
    assert parsed.source == "generated"


def test_fenced_json_is_tolerated() -> None:
    raw = '```json\n{"prompt": "What gets wetter the more it dries?", "answer": "A towel."}\n```'
    assert puzzle_mod._parse(raw) is not None


def test_technical_puzzles_are_rejected() -> None:
    raw = json.dumps(
        {
            "prompt": "A server returns a cached response. Why is the token count wrong?",
            "answer": "The model counted the prompt twice.",
        }
    )
    assert puzzle_mod._parse(raw) is None


def test_malformed_oversized_and_em_dashed_output_is_rejected() -> None:
    assert puzzle_mod._parse("not json at all") is None
    assert puzzle_mod._parse(json.dumps({"prompt": "", "answer": "x"})) is None
    assert puzzle_mod._parse(json.dumps({"prompt": "x" * 500, "answer": "y"})) is None
    assert (
        puzzle_mod._parse(
            json.dumps({"prompt": "A puzzle — with a dash in it.", "answer": "no"})
        )
        is None
    )


def test_get_puzzle_falls_back_to_the_bank_when_generation_fails(
    tmp_path: Path, monkeypatch
) -> None:
    bank, state = _bank(tmp_path), tmp_path / "state.json"

    async def failing_generate(date, ai_config):
        return None

    monkeypatch.setattr(puzzle_mod, "generate", failing_generate)
    result = asyncio.run(
        puzzle_mod.get_puzzle("2026-09-16", object(), bank, state)
    )

    assert result is not None
    assert result.source == "bank"


def test_get_puzzle_skips_generation_without_an_ai_config(tmp_path: Path) -> None:
    bank, state = _bank(tmp_path), tmp_path / "state.json"

    result = asyncio.run(puzzle_mod.get_puzzle("2026-09-16", None, bank, state))

    assert result.source == "bank"


def test_shipped_bank_is_usable_and_free_of_em_dashes() -> None:
    data = json.loads(Path("data/puzzles.json").read_text(encoding="utf-8"))
    puzzles = data["puzzles"]

    assert len(puzzles) >= 30
    assert len({p["id"] for p in puzzles}) == len(puzzles)
    for entry in puzzles:
        assert entry["prompt"].strip() and entry["answer"].strip()
        assert "—" not in entry["prompt"]
        assert "—" not in entry["answer"]
