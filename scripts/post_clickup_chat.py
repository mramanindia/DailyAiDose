"""Post the latest daily summary to a ClickUp chat channel.

Reads the newest markdown file from data/summaries/, reformats it into a
compact chat-friendly message (no oversized headings, bold linked titles,
small footer), and sends it via the ClickUp API v3.

Required environment variables:
    CLICKUP_API_TOKEN     Personal API token (Settings -> Apps -> API Token)
    CLICKUP_WORKSPACE_ID  Numeric workspace (team) id
    CLICKUP_CHANNEL_ID    Chat channel id
"""

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ClickUp chat messages are capped well above this; stay conservative so the
# message stays readable in the chat pane.
MAX_CHARS = 30000

HEADER_LOGO = "🌅"
HEADER_TITLE = "DailyAiDose for Unloq"
FOOTER = "*This is an automated message from Agent DailyAiDose managed by Aman*"

# Source lines rendered by the compact digest start with the source type.
_SOURCE_LINE_RE = re.compile(
    r"^(?P<type>rss|hackernews|reddit|github|twitter|telegram|gdelt|"
    r"google_news|openbb|ossinsight)(?P<rest>( · .*)?)$"
)
_ITEM_HEADING_RE = re.compile(
    r"^##\s+\[(?P<title>.+?)\]\((?P<url>\S+?)\)(\s+⭐️?\s*(?P<score>[\d.?]+)/10)?\s*$"
)
_STATS_RE = re.compile(r"^>\s*From (?P<total>\d+) items?, (?P<selected>\d+)\b.*")

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


def format_chat_message(digest_md: str, date: str) -> str:
    """Convert the digest markdown into a simple, readable chat message.

    🌅 **DailyAiDose for Unloq — 17 July 2026**
    *5 picks from 323 items*

    **1. Title of the story** ⭐ 8.0
    One-sentence plain-language summary.
    🔗 [Read more](url) · *Source · date*

    ...

    *footer*
    """
    header = f"{HEADER_LOGO} **{HEADER_TITLE} — {date}**"
    subtitle = ""
    intro_lines: list[str] = []
    items: list[dict] = []
    current: dict | None = None

    for raw_line in digest_md.splitlines():
        line = raw_line.rstrip()

        if line.startswith("# ") or line == "---" or line.startswith("<a id="):
            continue

        stats = _STATS_RE.match(line)
        if stats:
            subtitle = (
                f"*{stats.group('selected')} picks from "
                f"{stats.group('total')} items*"
            )
            continue

        heading = _ITEM_HEADING_RE.match(line)
        if heading:
            current = {
                "title": heading.group("title"),
                "url": heading.group("url"),
                "score": heading.group("score"),
                "summary": [],
                "source": "",
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

    blocks: list[str] = [header]
    if subtitle:
        blocks.append(subtitle)
    blocks.extend(intro_lines)

    for i, item in enumerate(items, start=1):
        score = f" ⭐ {item['score']}" if item["score"] else ""
        lines = [f"**{i}. {item['title']}**{score}"]
        if item["summary"]:
            lines.append(" ".join(item["summary"]))
        link_line = f"🔗 [Read more]({item['url']})"
        if item["source"]:
            link_line += f" · *{item['source']}*"
        lines.append(link_line)
        blocks.append("\n".join(lines))

    blocks.append(FOOTER)
    return "\n\n".join(blocks)


def main() -> int:
    token = os.environ.get("CLICKUP_API_TOKEN")
    workspace_id = os.environ.get("CLICKUP_WORKSPACE_ID")
    channel_id = os.environ.get("CLICKUP_CHANNEL_ID")
    if not all([token, workspace_id, channel_id]):
        print(
            "ClickUp delivery not fully configured "
            "(need CLICKUP_API_TOKEN, CLICKUP_WORKSPACE_ID, CLICKUP_CHANNEL_ID) — skipping."
        )
        return 0

    summary_path = latest_summary(Path("data/summaries"))
    if summary_path is None:
        print("No summary file found in data/summaries/")
        return 1

    now = datetime.now(timezone.utc)
    date = f"{now.day} {now.strftime('%B %Y')}"  # e.g. "22 July 2026"
    digest_md = summary_path.read_text(encoding="utf-8")
    content = truncate_markdown(format_chat_message(digest_md, date), MAX_CHARS)

    response = httpx.post(
        f"https://api.clickup.com/api/v3/workspaces/{workspace_id}/chat/channels/{channel_id}/messages",
        headers={"Authorization": token, "Content-Type": "application/json"},
        json={"type": "message", "content": content, "content_format": "text/md"},
        timeout=30,
    )
    print(f"ClickUp response: {response.status_code} {response.text[:300]}")
    return 0 if response.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
