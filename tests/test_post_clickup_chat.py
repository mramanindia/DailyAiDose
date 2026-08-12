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


def test_no_repeated_header_and_numbered_plain_titles() -> None:
    msg = format_chat_message(DIGEST, "16 July 2026")

    assert msg.startswith("## DailyAiDose for Unloq — 16 July 2026")
    # Old H1 header must not survive
    assert "# DailyAIdose" not in msg
    # H2 heading-links become numbered bold plain-text titles with the score
    # appended as plain text (no emoji)
    assert "**1. Big Eval News** · 9.0/10" in msg
    assert "**2. Cost News** · 7.0/10" in msg
    assert "⭐" not in msg
    # The title itself is no longer a link; a small Read more link is
    assert "[Big Eval News]" not in msg
    assert "[Read more](https://example.com/a)" in msg
    assert "[Read more](https://example.com/b)" in msg


def test_stats_sources_and_footer_are_small() -> None:
    msg = format_chat_message(DIGEST, "16 July 2026")

    assert "*2 picks from 134 items*" in msg
    # "rss" prefix dropped, feed name kept
    assert "*GNews: LLM Evals & Hallucination · Jul 15, 16:48*" in msg
    # hackernews prettified, submitter username dropped
    assert "*Hacker News · Jul 15, 15:47*" in msg
    assert msg.endswith(
        "*This is an automated message from Agent DailyAiDose managed by Aman*"
    )
    # Horizontal rules are dropped
    assert "---" not in msg


def test_summary_text_kept_as_plain_paragraph() -> None:
    msg = format_chat_message(DIGEST, "16 July 2026")

    assert "A new evaluation framework was released." in msg
    assert "Inference prices dropped." in msg


GROUPED_DIGEST = """# DailyAIdose - 2026-08-12

> From 210 items, 3 important content pieces were selected

---

### Evals & Observability

## [Big Eval News](https://example.com/a) ⭐️ 9.0/10

A new evaluation framework was released.

rss · GNews: LLM Evals & Hallucination · Aug 12, 04:00

---

## [Second Eval Story](https://example.com/b) ⭐️ 8.0/10

Another eval story.

hackernews · someone · Aug 12, 03:00

---

### Events & Conferences

## [DI Summit India](https://example.com/c) ⭐️ 7.5/10

Decision intelligence summit in Mumbai this November.

events · disummit.example.com · Aug 12, 02:00

---
"""


def test_grouped_digest_renders_flat_list_with_category_tags() -> None:
    msg = format_chat_message(GROUPED_DIGEST, "12 August 2026")

    # No section headings — one flat score-sorted list with inline code tags
    assert "### " not in msg
    assert "**1. Big Eval News** · 9.0/10 `Evals & Observability`" in msg
    assert "**2. Second Eval Story** · 8.0/10 `Evals & Observability`" in msg
    # Event items are highlighted with a calendar marker on the title line
    assert "**3. 📅 DI Summit India** · 7.5/10 `Events & Conferences`" in msg
    # Non-event items carry no marker
    assert "**1. Big Eval News** · 9.0/10" in msg
    # The events source type is prettified (type token dropped, domain kept)
    assert "*disummit.example.com · Aug 12, 02:00*" in msg


def test_text_chart_shows_counts_and_average_scores_per_category() -> None:
    msg = format_chat_message(GROUPED_DIGEST, "12 August 2026")

    # The overview is a fenced code block placed before the first section
    chart_start = msg.index("```")
    assert msg.index("*3 picks from 210 items*") < chart_start
    chart = msg[chart_start : msg.index("```", chart_start + 3)]
    assert "Evals & Observability" in chart
    assert "▇▇▇▇▇▇" in chart  # two picks → six blocks
    assert "2 picks · avg 8.5/10" in chart
    assert "1 pick  · avg 7.5/10" in chart
    assert chart_start < msg.index("**1. Big Eval News**")


def test_flat_digest_without_groups_has_no_sections_or_chart() -> None:
    msg = format_chat_message(DIGEST, "16 July 2026")
    assert "**1. Big Eval News** · 9.0" in msg
    assert "### " not in msg
    assert "```" not in msg
