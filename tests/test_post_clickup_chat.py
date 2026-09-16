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

    assert msg.startswith("## DailyAiDose for Unloq, 16 July 2026")
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


Puzzle = post_clickup_chat.Puzzle
count_items = post_clickup_chat.count_items
format_answer_reply = post_clickup_chat.format_answer_reply

EMPTY_DIGEST = """# DailyAIdose - 2026-09-16

> Scanned 312 items, none of them worth sharing.

A quiet day across the tracked sources.
"""

SINGLE_DIGEST = """# DailyAIdose - 2026-09-16

> From 294 items, 1 important content pieces were selected

---

### Evals & Observability

## [Lone Story](https://example.com/only) ⭐️ 8.4/10

The only thing that cleared the bar today.

rss · Simon Willison · Sep 15, 18:40

---
"""

PUZZLE = Puzzle(
    prompt="A notebook and a pen cost 110 together. The notebook costs 100 more.",
    answer="5. Most people say 10, but that would make 120 in total.",
)


def test_empty_digest_leads_with_scan_count_and_carries_the_puzzle() -> None:
    msg = format_chat_message(EMPTY_DIGEST, "16 September 2026", PUZZLE)

    assert msg.startswith("## DailyAiDose for Unloq, 16 September 2026")
    assert "Scanned 312 items today and found nothing worth sharing here" in msg
    assert "so here's a brain teaser for you instead." in msg
    assert PUZZLE.prompt in msg
    assert "Answer's in the thread." in msg
    # The answer never rides along in the main message
    assert PUZZLE.answer not in msg
    assert msg.endswith(post_clickup_chat.FOOTER)


def test_empty_digest_drops_pipeline_facing_prose_and_stats_line() -> None:
    msg = format_chat_message(EMPTY_DIGEST, "16 September 2026", PUZZLE)

    assert "A quiet day across the tracked sources." not in msg
    assert "picks from" not in msg
    assert "```" not in msg


def test_single_item_gets_the_story_plus_a_teaser_without_a_pick_count() -> None:
    msg = format_chat_message(SINGLE_DIGEST, "16 September 2026", PUZZLE)

    assert "Scanned 294 items today and found just one thing worth sharing" in msg
    assert "so here's a brain teaser to go with it." in msg
    # The lone story keeps its full rendering but loses the "1." numbering
    assert "**Lone Story** · 8.4/10 `Evals & Observability`" in msg
    assert "**1. Lone Story**" not in msg
    assert "[Read more](https://example.com/only) · *Simon Willison · Sep 15, 18:40*" in msg
    assert "1 picks from 294 items" not in msg
    assert PUZZLE.prompt in msg


def test_quiet_day_without_a_puzzle_still_reads_as_a_finished_message() -> None:
    msg = format_chat_message(EMPTY_DIGEST, "16 September 2026", None)

    assert "Scanned 312 items today and found nothing worth sharing here." in msg
    assert "brain teaser" not in msg
    assert msg.endswith(post_clickup_chat.FOOTER)


def test_missing_digest_falls_back_to_a_quiet_day_message() -> None:
    msg = format_chat_message("", "16 September 2026", PUZZLE)

    assert "Scanned the feeds today and found nothing worth sharing here" in msg
    assert PUZZLE.prompt in msg


def test_two_or_more_items_ignore_the_puzzle_entirely() -> None:
    with_puzzle = format_chat_message(DIGEST, "16 July 2026", PUZZLE)
    without = format_chat_message(DIGEST, "16 July 2026")

    assert with_puzzle == without
    assert "brain teaser" not in with_puzzle


def test_count_items_drives_the_sparse_branch() -> None:
    assert count_items(EMPTY_DIGEST) == 0
    assert count_items(SINGLE_DIGEST) == 1
    assert count_items(DIGEST) == 2


def test_answer_reply_is_a_short_labelled_line() -> None:
    assert format_answer_reply(PUZZLE) == f"**Answer:** {PUZZLE.answer}"


def test_no_em_dashes_in_any_message_shape() -> None:
    for msg in (
        format_chat_message(EMPTY_DIGEST, "16 September 2026", PUZZLE),
        format_chat_message(SINGLE_DIGEST, "16 September 2026", PUZZLE),
        format_chat_message(DIGEST, "16 July 2026"),
        format_answer_reply(PUZZLE),
    ):
        assert "—" not in msg
