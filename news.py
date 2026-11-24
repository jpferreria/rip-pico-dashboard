"""
Lightweight news headline fetcher for MicroPython (Pico W / Pico 2 W).

Fetches RSS feeds (BBC) and JSON feeds (Hacker News) with low RAM overhead
and caches results in memory.
"""

import gc
import time

try:
    import requests
except ImportError:
    import urequests as requests

FEEDS = {
    "world": ("BBC World", "rss", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    "tech": ("BBC Tech", "rss", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    "science": ("BBC Science", "rss", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
    "hn": ("Hacker News", "hn", "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=8"),
}

CACHE_TTL_MS = 10 * 60 * 1000  # 10 minutes
_cache = {}  # feed_id -> {"items": [...], "fetched_ms": int, "source": str}


def _unescape(text):
    text = text.strip()
    if text.startswith("<![CDATA[") and text.endswith("]]>"):
        text = text[9:-3].strip()
    return (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&apos;", "'")
    )


def _extract_tag(block, tag):
    start_tag = "<" + tag + ">"
    end_tag = "</" + tag + ">"
    s = block.find(start_tag)
    if s == -1:
        return ""
    s += len(start_tag)
    e = block.find(end_tag, s)
    if e == -1:
        return ""
    return _unescape(block[s:e])


def _fetch_rss(url, limit=8):
    items = []
    resp = None
    try:
        gc.collect()
        resp = requests.get(url, headers={"User-Agent": "PicoW-Dashboard/1.0"})
        if resp.status_code != 200:
            raise RuntimeError("HTTP {}".format(resp.status_code))

        buf = ""
        bytes_read = 0
        max_bytes = 36 * 1024

        while len(items) < limit and bytes_read < max_bytes:
            chunk = resp.raw.read(1024)
            if not chunk:
                break
            bytes_read += len(chunk)
            buf += chunk.decode("utf-8", "ignore")

            while len(items) < limit:
                start = buf.find("<item>")
                if start == -1:
                    # Keep tail in case "<item>" spans chunks
                    if len(buf) > 32:
                        buf = buf[-32:]
                    break
                end = buf.find("</item>", start)
                if end == -1:
                    buf = buf[start:]
                    break

                block = buf[start:end + 7]
                buf = buf[end + 7:]

                title = _extract_tag(block, "title")
                link = _extract_tag(block, "link")
                pub = _extract_tag(block, "pubDate")
                if title:
                    items.append({"title": title, "url": link, "meta": pub})
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        gc.collect()

    return items


def _fetch_hn(url, limit=8):
    items = []
    resp = None
    try:
        gc.collect()
        resp = requests.get(url, headers={"User-Agent": "PicoW-Dashboard/1.0"})
        if resp.status_code != 200:
            raise RuntimeError("HTTP {}".format(resp.status_code))

        data = resp.json()
        for hit in data.get("hits", [])[:limit]:
            title = hit.get("title") or ""
            if not title:
                continue
            obj_id = hit.get("objectID", "")
            link = hit.get("url") or ("https://news.ycombinator.com/item?id=" + str(obj_id))
            pts = hit.get("points")
            meta = "{} pts".format(pts) if pts is not None else ""
            items.append({"title": title, "url": link, "meta": meta})
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
        gc.collect()

    return items


def get_headlines(feed_id="world", force=False, limit=8):
    """Return cached or freshly fetched headlines for the requested feed."""
    if feed_id not in FEEDS:
        feed_id = "world"

    source_name, kind, url = FEEDS[feed_id]
    now = time.ticks_ms()
    cached = _cache.get(feed_id)

    if not force and cached and time.ticks_diff(now, cached["fetched_ms"]) < CACHE_TTL_MS:
        age_s = time.ticks_diff(now, cached["fetched_ms"]) // 1000
        return {
            "feed": feed_id,
            "source": source_name,
            "items": cached["items"],
            "age_s": age_s,
            "error": None,
        }

    try:
        if kind == "rss":
            items = _fetch_rss(url, limit=limit)
        else:
            items = _fetch_hn(url, limit=limit)

        _cache[feed_id] = {
            "items": items,
            "fetched_ms": time.ticks_ms(),
            "source": source_name,
        }
        return {
            "feed": feed_id,
            "source": source_name,
            "items": items,
            "age_s": 0,
            "error": None,
        }
    except Exception as exc:
        # Return stale cache if available, along with the error message
        fallback_items = cached["items"] if cached else []
        age_s = time.ticks_diff(now, cached["fetched_ms"]) // 1000 if cached else None
        return {
            "feed": feed_id,
            "source": source_name,
            "items": fallback_items,
            "age_s": age_s,
            "error": str(exc),
        }
