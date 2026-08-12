"""Events discovery scraper backed by DuckDuckGo web search.

Runs a configurable set of DuckDuckGo text searches for upcoming industry
events (conferences, summits, meetups) and maps each result into a
ContentItem so the rest of the pipeline (AI scoring, category quotas,
summarization) treats them like any other source.

Design notes:

* Uses the key-less `ddgs` package, the same backend as the enricher.
* Search results carry no publication date, so ``since`` cannot be applied;
  items are stamped with the fetch time and rely on the seen-items store
  (``filtering.skip_seen_days``) to avoid resurfacing the same page daily.
* Whether a result is actually a relevant, upcoming, attendable event is
  decided by the AI scoring pass, not here — this scraper only gathers
  candidates. The ``{year}`` placeholder in queries keeps them evergreen.
* Results are deduplicated by URL across queries; a failing query logs a
  warning and is skipped rather than aborting the batch.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlparse

import httpx

from .base import BaseScraper
from ..models import ContentItem, EventsSearchConfig, SourceType

logger = logging.getLogger(__name__)


class EventsSearchScraper(BaseScraper):
    """Scraper that discovers industry events via DuckDuckGo text search."""

    SOURCE_TYPE = SourceType.EVENTS

    def __init__(self, config: EventsSearchConfig, http_client: httpx.AsyncClient):
        super().__init__({"events_search": config}, http_client)
        self.events_config = config

    async def fetch(self, since: datetime) -> List[ContentItem]:
        """Run all configured event searches and return unique results.

        ``since`` is unused: web search results have no publication date.
        """
        if not self.events_config.enabled or not self.events_config.queries:
            return []

        year = str(datetime.now(timezone.utc).year)
        items: List[ContentItem] = []
        seen_urls: set[str] = set()

        for query in self.events_config.queries:
            resolved = query.replace("{year}", year)
            results = await asyncio.to_thread(self._search, resolved)
            for result in results:
                item = self._result_to_item(result, resolved, seen_urls)
                if item is not None:
                    items.append(item)

        return items

    def _search(self, query: str) -> list:
        """Run one DuckDuckGo text search, returning raw result dicts."""
        try:
            from ddgs import DDGS

            # Suppress primp "Impersonate ... does not exist" stderr warning
            stderr = sys.stderr
            sys.stderr = open(os.devnull, "w")
            try:
                ddgs = DDGS()
                results = ddgs.text(
                    query,
                    region=self.events_config.region,
                    max_results=self.events_config.max_results,
                )
            finally:
                sys.stderr.close()
                sys.stderr = stderr
            return list(results or [])
        except Exception as exc:
            logger.warning("Events search failed for query %r: %s", query, exc)
            return []

    def _result_to_item(
        self, result: dict, query: str, seen_urls: set[str]
    ) -> Optional[ContentItem]:
        """Map one search result into a ContentItem, skipping bad entries."""
        try:
            title = (result.get("title") or "").strip()
            url = (result.get("href") or "").strip()
            if not title or not url.startswith("http"):
                return None
            if url in seen_urls:
                return None
            seen_urls.add(url)

            domain = urlparse(url).hostname or "web"
            if domain.startswith("www."):
                domain = domain[4:]

            url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]

            return ContentItem(
                id=self._generate_id("events", "event", url_hash),
                source_type=self.SOURCE_TYPE,
                title=title,
                url=url,
                content=(result.get("body") or "").strip() or None,
                author=domain,
                published_at=datetime.now(timezone.utc),
                metadata={
                    "category": self.events_config.category,
                    "search_query": query,
                    "source_name": domain,
                },
            )
        except Exception as exc:
            logger.warning("Skipping invalid events search result: %s", exc)
            return None
