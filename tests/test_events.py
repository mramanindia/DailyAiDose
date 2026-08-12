"""Unit tests for the events search scraper."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import httpx

from src.models import EventsSearchConfig, SourceType
from src.scrapers.events import EventsSearchScraper


def _run_async(coro):
    return asyncio.run(coro)


def _make_scraper(config: EventsSearchConfig) -> EventsSearchScraper:
    return EventsSearchScraper(config, httpx.AsyncClient())


SINCE = datetime(2026, 8, 11, tzinfo=timezone.utc)


def test_disabled_or_empty_queries_return_nothing() -> None:
    scraper = _make_scraper(EventsSearchConfig(enabled=False, queries=["x"]))
    assert _run_async(scraper.fetch(SINCE)) == []

    scraper = _make_scraper(EventsSearchConfig(enabled=True, queries=[]))
    assert _run_async(scraper.fetch(SINCE)) == []


def test_results_become_content_items_with_events_category() -> None:
    config = EventsSearchConfig(
        enabled=True,
        queries=["decision intelligence conference {year} India"],
    )
    scraper = _make_scraper(config)

    results = [
        {
            "title": "DI Summit India 2026",
            "href": "https://www.disummit.example.com/2026",
            "body": "The decision intelligence summit, Mumbai, Nov 2026.",
        },
        {"title": "No link", "href": ""},  # skipped
    ]
    with patch.object(EventsSearchScraper, "_search", return_value=results) as search:
        items = _run_async(scraper.fetch(SINCE))

    year = str(datetime.now(timezone.utc).year)
    assert search.call_args[0][0] == f"decision intelligence conference {year} India"

    assert len(items) == 1
    item = items[0]
    assert item.source_type == SourceType.EVENTS
    assert item.id.startswith("events:event:")
    assert item.title == "DI Summit India 2026"
    assert item.author == "disummit.example.com"  # www. stripped
    assert item.metadata["category"] == "events"
    assert item.metadata["source_name"] == "disummit.example.com"
    assert "Mumbai" in item.content


def test_duplicate_urls_across_queries_are_dropped() -> None:
    config = EventsSearchConfig(enabled=True, queries=["q1", "q2"])
    scraper = _make_scraper(config)

    result = {"title": "Same event", "href": "https://example.com/event", "body": "b"}
    with patch.object(EventsSearchScraper, "_search", return_value=[result]):
        items = _run_async(scraper.fetch(SINCE))

    assert len(items) == 1


def test_failing_search_is_skipped_not_fatal() -> None:
    config = EventsSearchConfig(enabled=True, queries=["boom", "ok"])
    scraper = _make_scraper(config)

    good = [{"title": "Event", "href": "https://example.com/e", "body": "b"}]
    with patch.object(EventsSearchScraper, "_search", side_effect=[[], good]):
        items = _run_async(scraper.fetch(SINCE))

    assert len(items) == 1
