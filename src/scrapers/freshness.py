"""Guards against republished-but-old news reaching the digest.

A Google News RSS entry carries a ``pubDate`` that is the time Google last
indexed the article, not the time the publisher released it. A 2018 VMblog
piece was served on 2026-09-27 with ``Sun, 27 Sep 2026 14:56:21 GMT`` as its
date, cleared every date filter in the pipeline, and reached the channel as
current news.

Three independent defences live here:

1. ``bound_google_news_url`` puts a ``when:`` operator on Google News search
   feeds. Google's own ``when:`` filter reads the real publication date, so
   the stale article disappears at fetch time. This is the cheap fix and it
   catches the failure above on its own.
2. ``resolve_google_news_url`` turns the opaque ``news.google.com/rss/
   articles/CBMi...`` redirector into the publisher URL, which makes the
   digest link readable and gives the checks below something to work with.
3. ``published_date_from_url`` and ``published_date_from_html`` read the date
   the publisher itself states, so an item whose feed date and page date
   disagree can be dropped.

Verification is best effort by design. Plenty of publishers answer an
automated request with a 403 (VMblog does), and gutting the digest whenever
a site blocks us would be a worse failure than the one being fixed. An item
that cannot be checked is reported as ``unverified`` and the caller decides.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

GOOGLE_NEWS_HOSTS = {"news.google.com"}

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_PAGE_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Sources that date their own items honestly, so their pages are never
# fetched: arXiv, Reddit, Hacker News and GitHub all report real timestamps.
TRUSTED_HOSTS = (
    "arxiv.org",
    "reddit.com",
    "news.ycombinator.com",
    "github.com",
    "huggingface.co",
)

# "/2018/11/14/headline" and "/2018-11-14-headline"
_URL_DATE_RE = re.compile(r"/(?P<year>19|20\d{2})[/-](?P<month>0[1-9]|1[0-2])(?:[/-](?P<day>[0-3]\d))?(?:/|-|$)")

_META_DATE_KEYS = (
    "article:published_time",
    "article:published",
    "og:article:published_time",
    "datePublished",
    "date",
    "pubdate",
    "publish-date",
    "publication_date",
    "sailthru.date",
    "parsely-pub-date",
    "dc.date.issued",
)

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}

# "Published: November 14, 2018", "Posted on 14 November 2018"
_VISIBLE_DATE_RE = re.compile(
    r"(?:published|posted|updated)\s*(?:on|:)?\s*"
    r"(?:(?P<month_name>[A-Z][a-z]{2,8})\s+(?P<day>\d{1,2}),?\s+(?P<year>(?:19|20)\d{2})"
    r"|(?P<day2>\d{1,2})\s+(?P<month_name2>[A-Z][a-z]{2,8}),?\s+(?P<year2>(?:19|20)\d{2}))",
    re.IGNORECASE,
)


def is_google_news_url(url: str) -> bool:
    try:
        return urlparse(str(url)).netloc.lower().lstrip("www.") in GOOGLE_NEWS_HOSTS
    except ValueError:
        return False


def bound_google_news_url(url: str, since: datetime) -> str:
    """Add a ``when:`` time bound to a Google News search feed URL.

    Google News search returns its best matches regardless of age unless the
    query says otherwise, and the feed's own dates cannot be trusted to bound
    them. The ``when:`` operator reads the real publication date, so applying
    it here is what actually keeps old articles out.

    URLs that already carry a ``when:``/``after:`` operator, and every URL
    that is not a Google News search, are returned unchanged.
    """
    url = str(url)
    parsed = urlparse(url)
    if parsed.netloc.lower().lstrip("www.") not in GOOGLE_NEWS_HOSTS:
        return url
    if "/rss/search" not in parsed.path:
        return url

    params = parse_qs(parsed.query, keep_blank_values=True)
    query = (params.get("q") or [""])[0]
    if not query or re.search(r"\b(?:when|after|before):", query):
        return url

    params["q"] = [f"{query} {google_news_time_operator(since)}"]
    flat = urlencode({k: v[0] for k, v in params.items()}, quote_via=quote)
    return urlunparse(parsed._replace(query=flat))


def google_news_time_operator(since: datetime) -> str:
    """Build a ``when:`` operator covering the window back to ``since``."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    hours = max(1, int((datetime.now(timezone.utc) - since).total_seconds() // 3600) + 1)
    if hours <= 100:
        return f"when:{hours}h"
    return f"when:{max(1, round(hours / 24))}d"


async def resolve_google_news_url(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Resolve a Google News redirector to the publisher's own URL.

    The modern ``/rss/articles/CBMi...`` token is opaque, so resolution means
    loading the interstitial for its signature and asking Google's own
    endpoint to translate it. Returns None when anything along the way fails,
    which is expected often enough that callers must keep the original URL.
    """
    if not is_google_news_url(url):
        return None

    article_id = url.rstrip("/").split("/")[-1].split("?")[0]
    if not article_id:
        return None

    try:
        page = await client.get(
            url, headers={"User-Agent": _BROWSER_UA}, follow_redirects=True, timeout=15
        )
        signature = re.search(r'data-n-a-sg="([^"]+)"', page.text)
        timestamp = re.search(r'data-n-a-ts="([^"]+)"', page.text)
        if not signature or not timestamp:
            return None

        # Google expects the call wrapped three lists deep.
        request = json.dumps(
            [[[
                    "Fbv4je",
                    json.dumps(
                        [
                            "garturlreq",
                            [
                                ["X", "X", ["X", "X"], None, None, 1, 1, "US:en",
                                 None, 1, None, None, None, None, None, 0, 1],
                                "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0,
                            ],
                            article_id,
                            int(timestamp.group(1)),
                            signature.group(1),
                        ]
                    ),
                    None,
                    "generic",
            ]]]
        )
        response = await client.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            headers={
                "User-Agent": _BROWSER_UA,
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            },
            data={"f.req": request},
            timeout=15,
        )
        found = re.findall(
            r'https?://(?!news\.google|www\.google|accounts\.google)[^\\"\s]+',
            response.text,
        )
        return found[0] if found else None
    except Exception as exc:
        logger.debug("Could not resolve Google News url %s: %s", url[:60], exc)
        return None


def published_date_from_url(url: str) -> Optional[datetime]:
    """Read a publication date out of a URL path, if it carries one."""
    match = _URL_DATE_RE.search(urlparse(str(url)).path)
    if not match:
        return None
    try:
        return datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day") or 1),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None


def published_date_from_html(html: str) -> Optional[datetime]:
    """Read the publication date a page states about itself.

    Tries structured metadata first (meta tags, JSON-LD, <time datetime>),
    then the visible byline, which is all some publishers expose.
    """
    for key in _META_DATE_KEYS:
        pattern = (
            r'<meta[^>]+(?:property|name|itemprop)=["\']'
            + re.escape(key)
            + r'["\'][^>]+content=["\']([^"\']+)["\']'
        )
        match = re.search(pattern, html, re.IGNORECASE)
        if not match:
            pattern = (
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name|itemprop)=["\']'
                + re.escape(key)
                + r'["\']'
            )
            match = re.search(pattern, html, re.IGNORECASE)
        if match:
            parsed = _parse_iso(match.group(1))
            if parsed:
                return parsed

    match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    if match:
        parsed = _parse_iso(match.group(1))
        if parsed:
            return parsed

    match = re.search(r'<time[^>]+datetime=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if match:
        parsed = _parse_iso(match.group(1))
        if parsed:
            return parsed

    return _parse_visible(html)


def _parse_visible(html: str) -> Optional[datetime]:
    """Find a human-written publication line such as "Published: May 4, 2019"."""
    text = re.sub(r"<[^>]+>", " ", html[:200000])
    match = _VISIBLE_DATE_RE.search(text)
    if not match:
        return None
    name = match.group("month_name") or match.group("month_name2") or ""
    day = match.group("day") or match.group("day2")
    year = match.group("year") or match.group("year2")
    month = _MONTHS.get(name.lower()) or _MONTHS.get(name.lower()[:3] + "")
    if month is None:
        for full, number in _MONTHS.items():
            if full.startswith(name.lower()[:3]):
                month = number
                break
    if month is None:
        return None
    try:
        return datetime(int(year), month, int(day), tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _parse_iso(value: str) -> Optional[datetime]:
    """Parse the date formats publishers put in metadata."""
    value = (value or "").strip()
    if not value:
        return None
    candidate = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d %B %Y", "%B %d, %Y", "%a, %d %b %Y %H:%M:%S %z"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


async def verify_published_date(
    client: httpx.AsyncClient,
    url: str,
    claimed: Optional[datetime],
    max_age_days: int,
) -> Tuple[str, Optional[datetime], str]:
    """Check a story's real age against what its feed claimed.

    Returns a ``(verdict, observed_date, reason)`` triple where verdict is
    "fresh", "stale", or "unverified". Anything that cannot be checked is
    "unverified" rather than "stale": most publishers block automated
    requests, and dropping every one of those would empty the digest.
    """
    url = str(url)
    host = urlparse(url).netloc.lower()
    if any(host.endswith(trusted) for trusted in TRUSTED_HOSTS):
        return "fresh", claimed, "trusted source"

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    from_url = published_date_from_url(url)
    if from_url is not None and from_url < cutoff:
        return "stale", from_url, f"url path dates it {from_url:%Y-%m-%d}"

    try:
        response = await client.get(
            url, headers=_PAGE_HEADERS, follow_redirects=True, timeout=12
        )
        if response.status_code >= 400:
            return "unverified", None, f"page returned {response.status_code}"
        observed = published_date_from_html(response.text)
    except Exception as exc:
        return "unverified", None, f"page unreachable ({type(exc).__name__})"

    if observed is None:
        return "unverified", None, "no date on the page"
    if observed < cutoff:
        return "stale", observed, f"page dates it {observed:%Y-%m-%d}"
    return "fresh", observed, f"page dates it {observed:%Y-%m-%d}"
