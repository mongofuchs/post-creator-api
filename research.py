import re
import time
import html
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus

import feedparser
import requests

HEADERS = {
    "User-Agent": "PostCreator/1.0 (lokales Recherche-Tool;+https://github.com/local)",
    "Accept-Language": "de,en;q=0.8",
}
TIMEOUT = 12


def _clean(text, limit=400):
    if not text:
        return ""
    text = html.unescape(str(text))
    text = re.sub(r"<[^>]+>", " ", text)
    return text.replace("\n", " ").strip()[:limit]


def _item(title, url, summary="", published=0, source="", extra=""):
    return {
        "title": _clean(title, 220),
        "url": url or "",
        "summary": _clean(summary),
        "published": int(published or 0),
        "source": source,
        "extra": extra,
    }


def fetch_google_news(topic, lang="de"):
    tag = f"{lang.upper()}_EN" if lang == "en" else "DE_de".upper()
    url = (
        "https://news.google.com/rss/search?q=" + quote_plus(topic)
        + f"&hl=de&gl=DE&ceid={tag}"
    )
    d = feedparser.parse(requests.get(url, headers=HEADERS, timeout=TIMEOUT).text)
    out = []
    for e in d.entries[:25]:
        pub = 0
        if hasattr(e, "published_parsed") and e.published_parsed:
            pub = time.mktime(e.published_parsed)
        out.append(_item(e.title, e.link, e.get("summary", ""), pub, "Google News"))
    return out


def fetch_bing_news(topic, lang="de"):
    url = "https://www.bing.com/news/search?q=" + quote_plus(topic) + "&format=RSS&setmkt=de-DE"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except Exception:
        return []
    if resp.status_code != 200:
        return []
    d = feedparser.parse(resp.text)
    out = []
    for e in d.entries[:20]:
        pub = 0
        if hasattr(e, "published_parsed") and e.published_parsed:
            pub = time.mktime(e.published_parsed)
        out.append(_item(e.title, e.link, e.get("summary", ""), pub, "Bing News"))
    return out


def fetch_reddit(topic, lang="de"):
    url = "https://www.reddit.com/search.rss?q=" + quote_plus(topic) + "&sort=new&t=month&limit=25"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except Exception:
        return []
    d = feedparser.parse(resp.text)
    if not d.entries:
        return []
    out = []
    for e in d.entries[:20]:
        author = getattr(e, "author", "")
        sub = ""
        m = re.search(r"/r/([\w\-\u00c0-\uffff]+)/", getattr(e, "link", "") or "")
        if m:
            sub = m.group(1)
        pub = 0
        if hasattr(e, "published_parsed") and e.published_parsed:
            pub = time.mktime(e.published_parsed)
        out.append(
            _item(e.title, e.link, author, pub, "Reddit", extra=f"r/{sub}" if sub else "")
        )
    return out


def fetch_hackernews(topic, lang="de"):
    url = (
        "https://hn.algolia.com/api/v1/search?query=" + quote_plus(topic)
        + "&tags=story&hitsPerPage=15"
    )
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for h in r.json().get("hits", []):
        out.append(
            _item(
                h.get("title", ""),
                h.get("url") or ("https://news.ycombinator.com/item?id=" + str(h.get("objectID", ""))),
                "",
                h.get("created_at_i", 0),
                "Hacker News",
                extra=str(h.get("points", "")) + " pts",
            )
        )
    return out


def fetch_google_trends(geo="DE"):
    url = f"https://trends.google.com/trending/rss?geo={geo}"
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    d = feedparser.parse(resp.text)
    out = []
    for e in d.entries[:20]:
        out.append(_item(e.title, e.get("link", ""), e.get("trend Traff_num", ""), 0, "Google Trends"))
    return out


COLLECTORS = [
    ("news", fetch_google_news),
    ("bing", fetch_bing_news),
    ("reddit", fetch_reddit),
    ("hackernews", fetch_hackernews),
]


def _stopwords():
    base = {
        "the", "and", "for", "with", "from", "that", "this", "und", "der", "die",
        "das", "ist", "im", "in", "auf", "für", "mit", "den", "dem", "ein", "eine",
        "einer", "zu", "von", "wie", "was", "sich", "auch", "nicht", "als", "bei",
        "nach", "sind", "wird", "wurde", "haben", "sein", "de", "la", "le", "des",
        "en", "por", "con", "que", "all", "new", "about", "more", "best", "https",
        "http", "www", "com",
    }
    return base


def extract_keywords(texts, limit=10):
    words = {}
    stop = _stopwords()
    for t in texts:
        for w in re.findall(r"[\wäöüÄÖÜß]{3,}", t.lower()):
            if w in stop or w.isdigit():
                continue
            words[w] = words.get(w, 0) + 1
    return [w for w, c in sorted(words.items(), key=lambda x: -x[1])[:limit]]


def _norm_title(title):
    return " ".join(sorted(re.findall(r"[\wäöüÄÖÜß]+", title.lower()))[:12])


def dedupe(items):
    seen = set()
    out = []
    for it in sorted(items, key=lambda x: -x["published"]):
        key = _norm_title(it["title"])
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def score_items(items, now=None):
    now = now or time.time()
    for it in items:
        days = max(0.0, (now - it["published"]) / 86400.0) if it["published"] else 3.0
        it["score"] = round(max(0.0, 10 - days) * 0.4 + 1.0, 2)
    return items


def fetch_wikipedia_background(topic, lang="de"):
    def summary(url):
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        d = r.json()
        return {"title": d.get("title", topic), "extract": d.get("extract", "")[:600]}
    try:
        res = summary(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote_plus(topic.replace(' ', '_'))}")
        return res
    except Exception:
        try:
            return summary(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(topic.replace(' ', '_'))}")
        except Exception:
            return None


def _collect_for_query(query, lang="de"):
    result = {"items": [], "errors": {}}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {
            ex.submit(fn, query, lang): name
            for name, fn in COLLECTORS
        }
        for f in as_completed(futs):
            name = futs[f]
            try:
                data = f.result()
            except Exception as exc:
                result["errors"][name] = f"{type(exc).__name__}: {exc}"
                continue
            result["items"].extend(data)
    return result


def collect(topic, lang="de"):
    result = {"topic": topic, "items": [], "errors": {}, "trends": [], "background": None}
    queries = [topic]
    broad = topic
    for w in ["Dortmund", "Berlin", "München", "Hamburg", "Köln", "Frankfurt", "Stuttgart", "Leipzig"]:
        broad = broad.replace(w, "").strip()
    if broad and broad != topic:
        queries.append(broad)
    for q in queries:
        part = _collect_for_query(q, lang)
        result["errors"].update(part["errors"])
        result["items"].extend(part["items"])
        if len(result["items"]) >= 15:
            break
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_trends = ex.submit(fetch_google_trends, "DE")
        fut_wiki = ex.submit(fetch_wikipedia_background, topic, lang)
        try:
            result["trends"] = [it["title"] for it in fut_trends.result()]
        except Exception as exc:
            result["errors"]["trends"] = f"{type(exc).__name__}: {exc}"
        try:
            result["background"] = fut_wiki.result()
        except Exception as exc:
            result["errors"]["wikipedia"] = f"{type(exc).__name__}: {exc}"
    result["items"] = dedupe(score_items(result["items"]))[:30]
    result["keywords"] = extract_keywords([i["title"] for i in result["items"]] + [i["summary"] for i in result["items"]])
    return result
