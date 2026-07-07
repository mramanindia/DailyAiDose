"""Post the latest daily summary to a ClickUp chat channel.

Reads the newest markdown file from data/summaries/ and sends it as a
chat message via the ClickUp API v3.

Required environment variables:
    CLICKUP_API_TOKEN     Personal API token (Settings -> Apps -> API Token)
    CLICKUP_WORKSPACE_ID  Numeric workspace (team) id
    CLICKUP_CHANNEL_ID    Chat channel id
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ClickUp chat messages are capped well above this; stay conservative so the
# message stays readable in the chat pane.
MAX_CHARS = 30000


def latest_summary(summaries_dir: Path) -> Path | None:
    files = sorted(summaries_dir.glob("horizon-*-en.md"))
    return files[-1] if files else None


def truncate_markdown(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    return text[: cut if cut > 0 else limit] + "\n\n_…truncated_"


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

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    body = truncate_markdown(summary_path.read_text(encoding="utf-8"), MAX_CHARS)
    content = f"🤖 **DailyAIdose — {date}**\n\n{body}"

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
