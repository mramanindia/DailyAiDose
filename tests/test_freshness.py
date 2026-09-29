"""Guards for the stale-news failure of 2026-09-28.

A 2018 VMblog article reached the digest because Google News reported
"Sun, 27 Sep 2026 14:56:21 GMT" as its date. These tests pin the three
defences that stop that repeating.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx

from src.scrapers.freshness import (
    bound_google_news_url,
    is_google_news_url,
    published_date_from_html,
    published_date_from_url,
    verify_published_date,
)

GNEWS_FEED = (
    "https://news.google.com/rss/search?q=%22ThoughtSpot%22+OR+%22DataRobot%22"
    "&hl=en-US&gl=US&ceid=US:en"
)


def _since(hours: int = 24) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _query_of(url: str) -> str:
    return parse_qs(urlparse(url).query)["q"][0]


def test_google_news_search_feeds_get_a_time_bound() -> None:
    bounded = bound_google_news_url(GNEWS_FEED, _since())
    query = _query_of(bounded)

    # Google's when: operator reads the real publication date, unlike pubDate
    assert "when:" in query
    assert '"ThoughtSpot" OR "DataRobot"' in query
    # The other feed params survive untouched
    assert parse_qs(urlparse(bounded).query)["ceid"] == ["US:en"]


def test_long_windows_use_days_and_short_windows_use_hours() -> None:
    assert "when:25h" in _query_of(bound_google_news_url(GNEWS_FEED, _since(24)))
    assert "when:30d" in _query_of(
        bound_google_news_url(GNEWS_FEED, _since(24 * 30))
    )


def test_an_existing_operator_is_left_alone() -> None:
    for existing in ("when:7d", "after:2026-01-01", "before:2026-09-01"):
        url = f"https://news.google.com/rss/search?q=ai+{existing}&hl=en-US"
        assert bound_google_news_url(url, _since()) == url


def test_ordinary_feeds_are_never_rewritten() -> None:
    for url in (
        "https://simonwillison.net/atom/everything/",
        "https://arxiv.org/rss/cs.AI",
        "https://news.google.com/rss/topics/CAAqBwgKMKnf",  # not a search feed
    ):
        assert bound_google_news_url(url, _since()) == url


def test_google_news_urls_are_recognised() -> None:
    assert is_google_news_url("https://news.google.com/rss/articles/CBMiabc?oc=5")
    assert not is_google_news_url("https://vmblog.com/bylines/thoughtspot/")


def test_dates_are_read_out_of_url_paths() -> None:
    assert published_date_from_url(
        "https://techcrunch.com/2018/11/14/thoughtspot-datarobot/"
    ) == datetime(2018, 11, 14, tzinfo=timezone.utc)
    assert published_date_from_url("https://example.com/2019-03-07-a-post") == datetime(
        2019, 3, 7, tzinfo=timezone.utc
    )
    assert published_date_from_url("https://vmblog.com/bylines/thoughtspot/") is None


def test_dates_are_read_out_of_page_metadata() -> None:
    cases = {
        '<meta property="article:published_time" content="2018-11-14T10:00:00Z">',
        '<meta content="2018-11-14T10:00:00Z" name="article:published_time">',
        '<script type="application/ld+json">{"datePublished":"2018-11-14T10:00:00Z"}</script>',
        '<time datetime="2018-11-14T10:00:00Z">November 14</time>',
        "<p>David Marshall | Published: November 14, 2018</p>",
        "<p>Posted on 14 November 2018 by the team</p>",
    }
    for html in cases:
        parsed = published_date_from_html(html)
        assert parsed is not None, html
        assert (parsed.year, parsed.month, parsed.day) == (2018, 11, 14), html


def test_a_page_with_no_date_reads_as_unknown() -> None:
    assert published_date_from_html("<html><body>No date anywhere</body></html>") is None


_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _client(handler) -> httpx.AsyncClient:
    return _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler))


def _verify(handler, url: str, claimed: datetime | None = None, max_age: int = 14):
    async def run():
        async with _client(handler) as client:
            return await verify_published_date(client, url, claimed, max_age)

    return asyncio.run(run())


def test_the_2018_article_is_caught_by_its_own_page() -> None:
    """The exact regression: feed says today, the page says November 2018."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='<html><meta property="article:published_time" content="2018-11-14T10:00:00Z"></html>',
        )

    verdict, observed, reason = _verify(
        handler,
        "https://vmblog.com/bylines/thoughtspot-announces-partnership-with-datarobot/",
        claimed=datetime.now(timezone.utc),
    )

    assert verdict == "stale"
    assert observed.year == 2018
    assert "2018-11-14" in reason


def test_a_recent_page_passes() -> None:
    fresh = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=f'<meta property="article:published_time" content="{fresh}">'
        )

    verdict, _, _ = _verify(handler, "https://example.com/some-story/")
    assert verdict == "fresh"


def test_a_blocked_page_is_kept_rather_than_dropped() -> None:
    """VMblog answers automated requests with 403, and most publishers do."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    verdict, observed, reason = _verify(handler, "https://vmblog.com/bylines/story/")

    assert verdict == "unverified"
    assert observed is None
    assert "403" in reason


def test_an_unreachable_page_is_kept_rather_than_dropped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    verdict, _, reason = _verify(handler, "https://example.com/story/")
    assert verdict == "unverified"
    assert "unreachable" in reason


def test_an_old_url_path_is_caught_without_fetching_the_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the url alone was enough; no fetch should happen")

    verdict, observed, _ = _verify(
        handler, "https://techcrunch.com/2018/11/14/thoughtspot-datarobot/"
    )
    assert verdict == "stale"
    assert observed.year == 2018


def test_trusted_sources_are_never_fetched() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("arXiv dates itself honestly; no fetch should happen")

    for url in (
        "https://arxiv.org/abs/2609.30484",
        "https://www.reddit.com/r/MachineLearning/comments/abc/",
        "https://news.ycombinator.com/item?id=49868830",
        "https://github.com/foo/bar/releases",
    ):
        verdict, _, reason = _verify(handler, url)
        assert verdict == "fresh"
        assert reason == "trusted source"


# --- the orchestrator pass that applies all of the above -------------------

from types import SimpleNamespace  # noqa: E402

from rich.console import Console  # noqa: E402

from src.models import ContentItem, FilteringConfig, SourceType  # noqa: E402
from src.orchestrator import HorizonOrchestrator  # noqa: E402


def _item(item_id: str, url: str) -> ContentItem:
    return ContentItem(
        id=item_id,
        source_type=SourceType.RSS,
        title=item_id,
        url=url,
        published_at=datetime.now(timezone.utc),  # the feed's claim
        ai_score=8.0,
    )


def _orchestrator(**filtering) -> HorizonOrchestrator:
    orchestrator = HorizonOrchestrator.__new__(HorizonOrchestrator)
    orchestrator.config = SimpleNamespace(filtering=FilteringConfig(**filtering))
    orchestrator.console = Console(record=True)
    return orchestrator


def test_orchestrator_drops_the_stale_story_and_keeps_the_rest(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "old-story" in str(request.url):
            return httpx.Response(
                200, text='<meta property="article:published_time" content="2018-11-14T10:00:00Z">'
            )
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return httpx.Response(
            200, text=f'<meta property="article:published_time" content="{today}">'
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: _client(handler)
    )

    items = [
        _item("fresh", "https://example.com/new-story/"),
        _item("stale", "https://example.com/old-story/"),
    ]
    kept = asyncio.run(_orchestrator()._drop_stale_items(items))

    assert [i.id for i in kept] == ["fresh"]
    assert kept[0].metadata["verified_published_at"]


def test_verification_can_be_switched_off(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("verification is off; nothing should be fetched")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _client(handler))

    items = [_item("stale", "https://example.com/2018/11/14/old/")]
    kept = asyncio.run(
        _orchestrator(verify_published_dates=False)._drop_stale_items(items)
    )
    assert kept == items
