import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "post_clickup_chat",
    Path(__file__).parent.parent / "scripts" / "post_clickup_chat.py",
)
post_clickup_chat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(post_clickup_chat)

format_chat_message = post_clickup_chat.format_chat_message

DIGEST = """# DailyAIdose - 2026-07-16

> From 134 items, 2 important content pieces were selected

---

## [Big Eval News](https://example.com/a) ⭐️ 9.0/10

A new evaluation framework was released.

rss · GNews: LLM Evals & Hallucination · Jul 15, 16:48

---

## [Cost News](https://example.com/b) ⭐️ 7.0/10

Inference prices dropped.

hackernews · someone · Jul 15, 15:47

---
"""


def test_no_repeated_header_and_bold_titles() -> None:
    msg = format_chat_message(DIGEST, "16 July 2026")

    assert msg.startswith("🌅 **DailyAiDose for Unloq — 16 July 2026**")
    # Old H1 header must not survive
    assert "# DailyAIdose" not in msg
    # H2 item headings become bold links
    assert "**[Big Eval News](https://example.com/a)** ⭐️ 9.0/10" in msg
    assert "## " not in msg


def test_stats_sources_and_footer_are_small() -> None:
    msg = format_chat_message(DIGEST, "2026-07-16")

    assert "*2 picks from 134 items*" in msg
    assert "*rss · GNews: LLM Evals & Hallucination · Jul 15, 16:48*" in msg
    assert "*hackernews · someone · Jul 15, 15:47*" in msg
    assert msg.endswith(
        "*This is an automated message from Agent DailyAiDose managed by Aman*"
    )
    # Horizontal rules are dropped
    assert "---" not in msg


def test_summary_text_kept_as_plain_paragraph() -> None:
    msg = format_chat_message(DIGEST, "2026-07-16")

    assert "A new evaluation framework was released." in msg
    assert "Inference prices dropped." in msg
