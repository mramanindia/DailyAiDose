"""Post the latest daily summary to a ClickUp chat channel.

Reads the newest markdown file from data/summaries/, reformats it into a
compact chat-friendly message (no oversized headings, bold linked titles,
small footer), and sends it via the ClickUp API v3.

On a quiet day (no items, or a single item) the message carries a brain
teaser instead of an apology, with the answer posted as a reply in the
message thread so the channel can guess first.

Required environment variables:
    CLICKUP_API_TOKEN     Personal API token (Settings -> Apps -> API Token)
    CLICKUP_WORKSPACE_ID  Numeric workspace (team) id
    CLICKUP_CHANNEL_ID    Chat channel id
"""

import argparse
import asyncio
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.puzzle import Puzzle, get_puzzle  # noqa: E402

# ClickUp chat messages are capped well above this; stay conservative so the
# message stays readable in the chat pane.
MAX_CHARS = 30000

HEADER_TITLE = "DailyAiDose for Unloq"
FOOTER = "*This is an automated message from Agent DailyAiDose managed by Aman*"

# Markdown collapses consecutive blank lines, so use a line holding a single
# non-breaking space to force an extra visual gap between stories.
SPACER = "\u00a0"

# Source lines rendered by the compact digest start with the source type.
_SOURCE_LINE_RE = re.compile(
    r"^(?P<type>rss|hackernews|reddit|github|twitter|telegram|gdelt|"
    r"google_news|openbb|ossinsight|events)(?P<rest>( · .*)?)$"
)
_ITEM_HEADING_RE = re.compile(
    r"^##\s+\[(?P<title>.+?)\]\((?P<url>\S+?)\)(\s+⭐️?\s*(?P<score>[\d.?]+)/10)?\s*$"
)
_STATS_RE = re.compile(r"^>\s*From (?P<total>\d+) items?, (?P<selected>\d+)\b.*")
# The empty digest reports only a scanned count, with nothing selected.
_EMPTY_STATS_RE = re.compile(r"^>\s*(?:Analyzed|Scanned) (?P<total>\d+) items?\b.*")
# Category section headers emitted by the compact digest ("### 📅 Events").
_GROUP_HEADING_RE = re.compile(r"^###\s+(?P<name>.+?)\s*$")

# Friendly names for source types shown in the small info line. "rss" is
# dropped entirely — the feed name that follows it is enough.
_SOURCE_TYPE_LABELS = {
    "rss": None,
    "hackernews": "Hacker News",
    "reddit": None,  # the r/subreddit token that follows is enough
    "github": "GitHub",
    "google_news": "Google News",
    "twitter": "X",
    "telegram": "Telegram",
    "gdelt": "GDELT",
    "openbb": "OpenBB",
    "ossinsight": "OSS Insight",
    "events": None,  # the site domain that follows it is enough
}


def latest_summary(summaries_dir: Path) -> Path | None:
    files = sorted(summaries_dir.glob("horizon-*-en.md"))
    return files[-1] if files else None


def truncate_markdown(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    return text[: cut if cut > 0 else limit] + "\n\n_…truncated_"


def _pretty_source(line: str) -> str:
    """Condense a digest source line for the small info row.

    "rss · Simon Willison · Jul 15, 23:59"  ->  "Simon Willison · Jul 15, 23:59"
    "hackernews · someone · Jul 15, 18:12"  ->  "Hacker News · Jul 15, 18:12"
    """
    tokens = line.split(" · ")
    source_type = tokens[0]
    rest = tokens[1:]
    label = _SOURCE_TYPE_LABELS.get(source_type, source_type)
    if label == "Hacker News" and len(rest) >= 2:
        rest = rest[1:]  # drop the submitter username, keep date + discussion
    if label:
        rest = [label] + rest
    return " · ".join(rest) if rest else line


def _render_text_chart(items: list[dict]) -> str:
    """Render a per-category overview as an aligned text bar chart.

    ClickUp chat does not fetch external images, so the "chart" is a fenced
    code block — monospace alignment is what makes the bars readable.

    Evals & Observability  ▇▇▇     1 pick   · avg 8.0/10
    Events & Conferences   ▇▇▇▇▇▇  2 picks  · avg 7.2/10
    """
    groups: dict[str, list[float | None]] = {}
    for item in items:
        if not item["group"]:
            return ""  # ungrouped digest: no overview
        score = item["score"]
        groups.setdefault(item["group"], []).append(
            float(score) if score and score != "?" else None
        )

    if len(groups) < 2:
        return ""  # a one-category chart says nothing

    label_width = max(len(g) for g in groups)
    bar_width = 3 * max(len(s) for s in groups.values())
    lines = []
    for group, scores in groups.items():
        bar = "▇" * (3 * len(scores))
        count = f"{len(scores)} pick" + ("s" if len(scores) > 1 else " ")
        known = [s for s in scores if s is not None]
        avg = f" · avg {sum(known) / len(known):.1f}/10" if known else ""
        lines.append(
            f"{group.ljust(label_width)}  {bar.ljust(bar_width)}  {count}{avg}"
        )
    return "```\n" + "\n".join(lines) + "\n```"


def _format_item(item: dict, label: str = "") -> str:
    """Render one story as a bold title line, a summary, and a link row."""
    score = f" \u00b7 {item['score']}/10" if item["score"] else ""
    tag = f" `{item['group']}`" if item["group"] else ""
    # Conferences/events are attendable and time-bound, so they carry a
    # calendar marker, the one deliberate emoji in the digest.
    marker = "\U0001f4c5 " if "event" in item["group"].lower() else ""
    lines = [f"**{label}{marker}{item['title']}**{score}{tag}"]
    if item["summary"]:
        lines.append(" ".join(item["summary"]))
    link_line = f"[Read more]({item['url']})"
    if item["source"]:
        link_line += f" \u00b7 *{item['source']}*"
    lines.append(link_line)
    return "\n".join(lines)


def _format_sparse_message(
    header: str,
    items: list[dict],
    total_fetched: int | None,
    puzzle: Puzzle | None,
) -> str:
    """Build the message for a day with nothing, or one story, to report.

    A quiet day still has to be worth opening, so the message says what was
    scanned and then hands over a brain teaser. The digest stats line and the
    category chart are dropped: "1 pick from 294 items" undersells the one
    story that did make it.
    """
    scanned = (
        f"Scanned {total_fetched} items today"
        if total_fetched
        else "Scanned the feeds today"
    )
    if items:
        lead = f"{scanned} and found just one thing worth sharing"
        lead += ", so here's a brain teaser to go with it." if puzzle else "."
    else:
        lead = f"{scanned} and found nothing worth sharing here"
        lead += ", so here's a brain teaser for you instead." if puzzle else "."

    sections = [f"{header}\n\n{lead}"]
    sections.extend(_format_item(item) for item in items)
    if puzzle is not None:
        sections.append(f"{puzzle.prompt}\n\nAnswer's in the thread.")
    sections.append(FOOTER)
    return f"\n\n{SPACER}\n\n".join(sections)


def format_answer_reply(puzzle: Puzzle) -> str:
    """The threaded reply carrying the puzzle answer."""
    return f"**Answer:** {puzzle.answer}"


def count_items(digest_md: str) -> int:
    """How many stories the digest holds, without building the message."""
    return sum(
        1 for line in digest_md.splitlines() if _ITEM_HEADING_RE.match(line.rstrip())
    )


def format_chat_message(
    digest_md: str, date: str, puzzle: Puzzle | None = None
) -> str:
    """Convert the digest markdown into a simple, readable chat message.

    ## DailyAiDose for Unloq, 17 July 2026
    *5 picks from 323 items*
    [text bar chart of picks per category]

    **1. Title of the story** \u00b7 8.0/10 `Events & Conferences`
    One-sentence plain-language summary.
    [Read more](url) \u00b7 *Source \u00b7 date*

    ...

    *footer*

    Items are one flat score-sorted list; the category appears as an inline
    code tag at the end of the title line, not as section headers. Formatting
    is deliberately emoji-free (per user preference, 2026-08-12) with one
    exception: event/conference items carry a calendar marker so they stand
    out as attendable and time-bound.

    Fewer than two stories takes the sparse layout instead, which leads with
    the scan count and carries a brain teaser.
    """
    header = f"## {HEADER_TITLE}, {date}"
    subtitle = ""
    total_fetched: int | None = None
    intro_lines: list[str] = []
    items: list[dict] = []
    current: dict | None = None
    current_group = ""

    for raw_line in digest_md.splitlines():
        line = raw_line.rstrip()

        if line.startswith("# ") or line == "---" or line.startswith("<a id="):
            continue

        stats = _STATS_RE.match(line)
        if stats:
            total_fetched = int(stats.group("total"))
            subtitle = (
                f"*{stats.group('selected')} picks from "
                f"{stats.group('total')} items*"
            )
            continue

        empty_stats = _EMPTY_STATS_RE.match(line)
        if empty_stats:
            total_fetched = int(empty_stats.group("total"))
            continue

        group = _GROUP_HEADING_RE.match(line)
        if group:
            current_group = group.group("name")
            continue

        heading = _ITEM_HEADING_RE.match(line)
        if heading:
            current = {
                "title": heading.group("title"),
                "url": heading.group("url"),
                "score": heading.group("score"),
                "summary": [],
                "source": "",
                "group": current_group,
            }
            items.append(current)
            continue

        if not line:
            continue

        source = _SOURCE_LINE_RE.match(line)
        if source and current is not None:
            current["source"] = _pretty_source(line)
            continue

        if current is not None:
            current["summary"].append(line)
        elif line.startswith("> "):
            intro_lines.append(line[2:])
        else:
            intro_lines.append(line)

    # A quiet day drops the digest's own intro prose, which is written for
    # whoever is tuning the pipeline rather than for the channel.
    if len(items) < 2:
        return _format_sparse_message(header, items, total_fetched, puzzle)

    intro = [header]
    if subtitle:
        intro.append(subtitle)
    chart = _render_text_chart(items)
    if chart:
        intro.append(chart)
    intro.extend(intro_lines)

    # One flat list, best story first; the digest file groups items by
    # category section, so re-sort by score for the chat message.
    def sort_key(item: dict) -> float:
        try:
            return float(item["score"])
        except (TypeError, ValueError):
            return 0.0

    items.sort(key=sort_key, reverse=True)

    sections: list[str] = ["\n\n".join(intro)]
    sections.extend(
        _format_item(item, f"{i}. ") for i, item in enumerate(items, start=1)
    )
    sections.append(FOOTER)
    return f"\n\n{SPACER}\n\n".join(sections)


def _load_ai_config():
    """AI config for puzzle generation, or None to fall back to the bank."""
    try:
        from src.storage.manager import StorageManager

        ai_config = StorageManager().load_config().ai
    except Exception as exc:  # missing config.json, bad JSON, anything
        print(f"No AI config for puzzle generation ({exc}); using the bank.")
        return None

    if not os.environ.get(ai_config.api_key_env or ""):
        print(
            f"{ai_config.api_key_env} is not set; using the puzzle bank."
        )
        return None
    return ai_config


def _post(url: str, payload: dict, token: str, label: str) -> dict | None:
    """POST to ClickUp with retries, returning the response body on success.

    ClickUp occasionally returns transient 5xx errors; retry a few times
    before giving up.
    """
    attempts = 3
    for attempt in range(1, attempts + 1):
        response = httpx.post(
            url,
            headers={"Authorization": token, "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        print(
            f"ClickUp {label} response (attempt {attempt}/{attempts}): "
            f"{response.status_code} {response.text[:300]}"
        )
        if response.is_success:
            try:
                body = response.json()
            except ValueError:
                return {}
            return body if isinstance(body, dict) else {}
        if response.status_code < 500:
            break  # client error, retrying the same payload will not help
        if attempt < attempts:
            time.sleep(10 * attempt)
    return None


def _message_id(body: dict) -> str | None:
    """Pull the created message id out of a ClickUp response body."""
    for candidate in (body, body.get("data") if isinstance(body, dict) else None):
        if isinstance(candidate, dict) and candidate.get("id"):
            return str(candidate["id"])
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Post the latest daily summary to a ClickUp chat channel."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the message and any thread reply instead of posting.",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    date = f"{now.day} {now.strftime('%B %Y')}"  # e.g. "22 July 2026"

    summary_path = latest_summary(Path("data/summaries"))
    if summary_path is not None and today not in summary_path.name:
        # A run that fetched nothing writes no summary for today. Posting the
        # newest file on disk would replay an old digest under today's date.
        print(
            f"Newest summary {summary_path.name} is not today's; "
            "treating today as a quiet day."
        )
        summary_path = None

    digest_md = summary_path.read_text(encoding="utf-8") if summary_path else ""

    # Nothing to report, or a single story, gets a brain teaser alongside it.
    puzzle = None
    if count_items(digest_md) < 2:
        puzzle = asyncio.run(get_puzzle(today, _load_ai_config()))
        if puzzle is None:
            print("No puzzle available; posting the quiet day message plain.")
        else:
            print(f"Quiet day: using a {puzzle.source} puzzle.")

    content = truncate_markdown(
        format_chat_message(digest_md, date, puzzle), MAX_CHARS
    )

    if args.dry_run:
        print("\n===== message =====\n")
        print(content)
        if puzzle is not None:
            print("\n===== thread reply =====\n")
            print(format_answer_reply(puzzle))
        return 0

    token = os.environ.get("CLICKUP_API_TOKEN")
    workspace_id = os.environ.get("CLICKUP_WORKSPACE_ID")
    channel_id = os.environ.get("CLICKUP_CHANNEL_ID")
    if not all([token, workspace_id, channel_id]):
        print(
            "ClickUp delivery not fully configured "
            "(need CLICKUP_API_TOKEN, CLICKUP_WORKSPACE_ID, CLICKUP_CHANNEL_ID) "
            "- skipping."
        )
        return 0

    base = f"https://api.clickup.com/api/v3/workspaces/{workspace_id}/chat"
    body = _post(
        f"{base}/channels/{channel_id}/messages",
        {"type": "message", "content": content, "content_format": "text/md"},
        token,
        "message",
    )
    if body is None:
        return 1

    # The answer goes in the thread so the channel can guess first. A failed
    # reply is not worth failing the run over: the puzzle is already posted.
    if puzzle is not None:
        message_id = _message_id(body)
        if message_id is None:
            print("No message id in the response; skipping the answer reply.")
        elif _post(
            f"{base}/messages/{message_id}/replies",
            {
                "type": "message",
                "content": format_answer_reply(puzzle),
                "content_format": "text/md",
            },
            token,
            "answer reply",
        ) is None:
            print("Puzzle posted, but the answer reply failed to send.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
