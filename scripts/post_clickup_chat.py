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
    r"^(rss|hackernews|reddit|github|twitter|telegram|gdelt|google_news|"
    r"openbb|ossinsight)\b.*"
)
_ITEM_HEADING_RE = re.compile(r"^##\s+(?P<rest>\[.*)$")
_STATS_RE = re.compile(r"^>\s*From (?P<total>\d+) items?, (?P<selected>\d+)\b.*")


def latest_summary(summaries_dir: Path) -> Path | None:
    files = sorted(summaries_dir.glob("horizon-*-en.md"))
    return files[-1] if files else None


def truncate_markdown(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    return text[: cut if cut > 0 else limit] + "\n\n_…truncated_"


def format_chat_message(digest_md: str, date: str) -> str:
    """Convert the digest markdown into a compact chat message.

    - single bold header line (no repeated H1)
    - item titles become bold links instead of H2 headings
    - source lines become small (italic) text
    - horizontal rules dropped; spacing separates items
    - small automated-message footer at the bottom
    """
    lines_out: list[str] = [f"{HEADER_LOGO} **{HEADER_TITLE} — {date}**"]

    for raw_line in digest_md.splitlines():
        line = raw_line.rstrip()

        if line.startswith("# ") or line == "---" or line.startswith("<a id="):
            continue

        stats = _STATS_RE.match(line)
        if stats:
            lines_out.append(
                f"*{stats.group('selected')} picks from "
                f"{stats.group('total')} items*"
            )
            continue
        if line.startswith("> "):
            lines_out.append(f"*{line[2:]}*")
            continue

        heading = _ITEM_HEADING_RE.match(line)
        if heading:
            rest = heading.group("rest")
            # "[title](url) ⭐️ 7.0/10" -> "**[title](url)** ⭐️ 7.0/10"
            match = re.match(r"^(\[.*?\]\(.*?\))(.*)$", rest)
            if match:
                lines_out.append(f"**{match.group(1)}**{match.group(2)}")
            else:
                lines_out.append(f"**{rest}**")
            continue

        if _SOURCE_LINE_RE.match(line):
            lines_out.append(f"*{line}*")
            continue

        lines_out.append(line)

    # Collapse runs of blank lines and trim the edges.
    collapsed: list[str] = []
    for line in lines_out:
        if line == "" and (not collapsed or collapsed[-1] == ""):
            continue
        collapsed.append(line)
    while collapsed and collapsed[-1] == "":
        collapsed.pop()

    body = "\n\n".join(line for line in collapsed if line != "")
    return f"{body}\n\n{FOOTER}"


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
