"""
Auto-tag suggestion module.

Scrapers return a list of dicts:
    {"name": str, "source": str, "confidence": float (0-1)}

Add new scrapers by implementing the _Scraper protocol and registering
them in _SCRAPERS. Each scraper receives the post source URL string and
the post model; it may do network I/O via net.download().
"""

import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from szurubooru import config, errors, model
from szurubooru.func import net

logger = logging.getLogger(__name__)

Suggestion = Dict  # {name, source, confidence}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REDDIT_USER_AGENT = (
    "custombooru-tagger/1.0 (auto tag suggestion; contact site admin)"
)


def _fetch_json(url: str, user_agent: Optional[str] = None) -> Optional[dict]:
    req = urllib.request.Request(url)
    ua = user_agent or config.config.get("user_agent") or "custombooru/1.0"
    req.add_header("User-Agent", ua)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("autotagger fetch failed for %s: %s", url, exc)
        return None


def _slug_to_tag(text: str) -> str:
    """Normalise a freeform string into a tag-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text.strip("_")


def _make(name: str, source: str, confidence: float = 1.0) -> Suggestion:
    tag = _slug_to_tag(name)
    if not tag:
        return None
    return {"name": tag, "source": source, "confidence": confidence}


def _dedupe(suggestions: List[Suggestion]) -> List[Suggestion]:
    seen: Dict[str, Suggestion] = {}
    for s in suggestions:
        if s is None:
            continue
        existing = seen.get(s["name"])
        if existing is None or s["confidence"] > existing["confidence"]:
            seen[s["name"]] = s
    return sorted(seen.values(), key=lambda x: -x["confidence"])


# ---------------------------------------------------------------------------
# Reddit scraper
# ---------------------------------------------------------------------------

_REDDIT_RE = re.compile(
    r"https?://(?:www\.|old\.)?reddit\.com/r/([^/]+)/comments/([a-z0-9]+)",
    re.IGNORECASE,
)
_REDDIT_SHORT_RE = re.compile(
    r"https?://redd\.it/([a-z0-9]+)", re.IGNORECASE
)


def _scrape_reddit(source_url: str) -> List[Suggestion]:
    m = _REDDIT_RE.match(source_url) or _REDDIT_SHORT_RE.match(source_url)
    if not m:
        return []

    if len(m.groups()) == 2:
        subreddit, post_id = m.group(1), m.group(2)
    else:
        post_id = m.group(1)
        subreddit = None

    api_url = f"https://www.reddit.com/comments/{post_id}.json?limit=1"
    data = _fetch_json(api_url, _REDDIT_USER_AGENT)
    if not data or not isinstance(data, list) or len(data) == 0:
        return []

    try:
        post_data = data[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError):
        return []

    results: List[Suggestion] = []

    # Subreddit as tag
    sr = post_data.get("subreddit", subreddit)
    if sr:
        results.append(_make(sr, "reddit:subreddit", 0.9))

    # Flair text
    flair = post_data.get("link_flair_text") or ""
    for part in re.split(r"[,|/\s]+", flair):
        if part.strip():
            results.append(_make(part.strip(), "reddit:flair", 0.75))

    # Extract bracketed tags from title, e.g. "[OC] [NSFW] Cat photo"
    title = post_data.get("title", "")
    for bracket in re.findall(r"\[([^\]]+)\]", title):
        results.append(_make(bracket, "reddit:title_tag", 0.6))

    # Author
    author = post_data.get("author", "")
    if author and author not in ("[deleted]", "[removed]"):
        results.append(_make("u_" + author, "reddit:author", 0.4))

    return [r for r in results if r]


# ---------------------------------------------------------------------------
# RedGIFs scraper
# ---------------------------------------------------------------------------

_REDGIFS_RE = re.compile(
    r"https?://(?:www\.)?redgifs\.com/watch/([a-z0-9_-]+)", re.IGNORECASE
)
_REDGIFS_EMBED_RE = re.compile(
    r"https?://(?:www\.)?redgifs\.com/ifr/([a-z0-9_-]+)", re.IGNORECASE
)


def _scrape_redgifs(source_url: str) -> List[Suggestion]:
    m = _REDGIFS_RE.match(source_url) or _REDGIFS_EMBED_RE.match(source_url)
    if not m:
        return []

    gif_id = m.group(1).lower()

    # RedGIFs public API — no auth needed for metadata
    api_url = f"https://api.redgifs.com/v2/gifs/{gif_id}"
    data = _fetch_json(api_url)
    if not data:
        return []

    results: List[Suggestion] = []
    gif = data.get("gif", {})

    for tag in gif.get("tags", []):
        results.append(_make(tag, "redgifs:tag", 0.85))

    username = gif.get("userName", "")
    if username:
        results.append(_make("u_" + username, "redgifs:author", 0.4))

    return [r for r in results if r]


# ---------------------------------------------------------------------------
# Generic booru scraper (Danbooru-compatible API)
# Covers: Danbooru, Gelbooru (v0.2 API), Safebooru, Rule34.xxx, e621, etc.
# ---------------------------------------------------------------------------

_BOORU_PATTERNS = [
    # Danbooru — /posts/<id>
    (
        re.compile(
            r"https?://danbooru\.donmai\.us/posts/(\d+)", re.IGNORECASE
        ),
        "https://danbooru.donmai.us/posts/{id}.json",
        "data.tag_string",
        "danbooru",
    ),
    # Gelbooru — index.php?page=post&s=view&id=<id>
    (
        re.compile(
            r"https?://(?:www\.)?gelbooru\.com.*[?&]id=(\d+)", re.IGNORECASE
        ),
        "https://gelbooru.com/index.php?page=dapi&s=post&q=index&json=1&id={id}",
        "post.0.tags",
        "gelbooru",
    ),
    # Rule34.xxx
    (
        re.compile(
            r"https?://rule34\.xxx.*[?&]id=(\d+)", re.IGNORECASE
        ),
        "https://rule34.xxx/index.php?page=dapi&s=post&q=index&json=1&id={id}",
        "0.tags",
        "rule34",
    ),
    # Safebooru
    (
        re.compile(
            r"https?://safebooru\.org.*[?&]id=(\d+)", re.IGNORECASE
        ),
        "https://safebooru.org/index.php?page=dapi&s=post&q=index&json=1&id={id}",
        "0.tags",
        "safebooru",
    ),
    # e621 — /posts/<id>
    (
        re.compile(
            r"https?://e621\.net/posts/(\d+)", re.IGNORECASE
        ),
        "https://e621.net/posts/{id}.json",
        "post.tags_string",
        "e621",
    ),
    # Konachan
    (
        re.compile(
            r"https?://konachan\.(?:com|net)/post/show/(\d+)", re.IGNORECASE
        ),
        "https://konachan.com/post.json?tags=id:{id}",
        "0.tags",
        "konachan",
    ),
    # Yande.re
    (
        re.compile(
            r"https?://yande\.re/post/show/(\d+)", re.IGNORECASE
        ),
        "https://yande.re/post.json?tags=id:{id}",
        "0.tags",
        "yandere",
    ),
]


def _deep_get(obj, dotpath: str):
    """Traverse a dot-separated path through nested dicts/lists."""
    for part in dotpath.split("."):
        if obj is None:
            return None
        if isinstance(obj, list):
            try:
                obj = obj[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


def _scrape_booru(source_url: str) -> List[Suggestion]:
    for pattern, api_template, tag_path, source_name in _BOORU_PATTERNS:
        m = pattern.search(source_url)
        if not m:
            continue
        post_id = m.group(1)
        api_url = api_template.format(id=post_id)
        data = _fetch_json(api_url)
        if not data:
            return []
        tag_string = _deep_get(data, tag_path)
        if not tag_string:
            return []
        tags = str(tag_string).split()
        return [
            _make(t, f"{source_name}:tag", 0.95)
            for t in tags
            if t
        ]
    return []


# ---------------------------------------------------------------------------
# Pixiv scraper (basic — only works with public posts)
# ---------------------------------------------------------------------------

_PIXIV_RE = re.compile(
    r"https?://(?:www\.)?pixiv\.net/(?:en/)?artworks/(\d+)", re.IGNORECASE
)


def _scrape_pixiv(source_url: str) -> List[Suggestion]:
    m = _PIXIV_RE.match(source_url)
    if not m:
        return []
    illust_id = m.group(1)
    # Pixiv requires login for full API; this hits the embed endpoint
    # which is public and returns basic tag data.
    api_url = f"https://www.pixiv.net/ajax/illust/{illust_id}?lang=en"
    req = urllib.request.Request(api_url)
    req.add_header("Referer", "https://www.pixiv.net/")
    req.add_header(
        "User-Agent",
        config.config.get("user_agent") or "custombooru/1.0",
    )
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("pixiv fetch failed for %s: %s", source_url, exc)
        return []

    results: List[Suggestion] = []
    body = data.get("body", {})

    for tag_obj in body.get("tags", {}).get("tags", []):
        tag_en = tag_obj.get("tag", "")
        if tag_en:
            results.append(_make(tag_en, "pixiv:tag", 0.9))
        romaji = (tag_obj.get("romaji") or "").strip()
        if romaji and romaji.lower() != tag_en.lower():
            results.append(_make(romaji, "pixiv:romaji", 0.7))

    user_name = body.get("userName", "")
    if user_name:
        results.append(_make("u_" + user_name, "pixiv:author", 0.4))

    return [r for r in results if r]


# ---------------------------------------------------------------------------
# Twitter / X scraper (unauthenticated — limited, uses oembed title only)
# ---------------------------------------------------------------------------

_TWITTER_RE = re.compile(
    r"https?://(?:www\.)?(?:twitter|x)\.com/[^/]+/status/(\d+)",
    re.IGNORECASE,
)


def _scrape_twitter(source_url: str) -> List[Suggestion]:
    if not _TWITTER_RE.match(source_url):
        return []
    oembed_url = (
        "https://publish.twitter.com/oembed?url="
        + urllib.parse.quote(source_url, safe="")
    )
    data = _fetch_json(oembed_url)
    if not data:
        return []
    results: List[Suggestion] = []
    # Extract #hashtags from the oembed HTML snippet
    html = data.get("html", "")
    for tag in re.findall(r"#([A-Za-z0-9_]+)", html):
        results.append(_make(tag, "twitter:hashtag", 0.7))
    author = data.get("author_name", "")
    if author:
        results.append(_make("u_" + author, "twitter:author", 0.4))
    return [r for r in results if r]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_SCRAPERS = [
    _scrape_reddit,
    _scrape_redgifs,
    _scrape_booru,
    _scrape_pixiv,
    _scrape_twitter,
]


def suggest(post: model.Post) -> List[Suggestion]:
    """
    Return de-duplicated tag suggestions for *post* based on its source URL(s).
    Each source line is tried against every scraper.
    """
    if not post.source:
        return []

    all_suggestions: List[Suggestion] = []
    for source_line in post.source.split("\n"):
        source_line = source_line.strip()
        if not source_line:
            continue
        for scraper in _SCRAPERS:
            try:
                all_suggestions.extend(scraper(source_line))
            except Exception as exc:
                logger.warning(
                    "autotagger scraper %s failed: %s", scraper.__name__, exc
                )

    return _dedupe(all_suggestions)
