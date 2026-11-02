import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "PostCreator/1.0 (lokales Tool fuer lizenzfreie Medien)",
}
TIMEOUT = 10
OPENVERSE_TIMEOUT = 5
LIMIT_PER_QUERY = 8
MAX_RESULTS = 32
MAX_SEARCH_QUERIES = 5
MAX_OPENVERSE_QUERIES = 2

STOP_WORDS = {
    "aber", "als", "am", "an", "auf", "aus", "bei", "das", "dem", "den",
    "der", "des", "die", "ein", "eine", "einer", "eines", "für", "im",
    "in", "ist", "mit", "oder", "und", "von", "vor", "zu", "zum", "zur",
    "the", "and", "for", "from", "in", "of", "on", "the", "to", "with",
}


class MediaSearchError(RuntimeError):
    """Raised when every media provider request fails."""


def _build_search_queries(*query_groups):
    phrases = []
    phrase_words = []
    words = []

    for group in query_groups:
        if isinstance(group, str):
            group = [group]
        for value in group or []:
            if not isinstance(value, str):
                continue
            phrase = re.sub(r"\s+", " ", value).strip()
            if not phrase:
                continue
            if phrase not in phrases:
                phrases.append(phrase)

            tokens = [
                token
                for token in re.findall(r"[^\W_]+", phrase, flags=re.UNICODE)
                if token.casefold() not in STOP_WORDS
                and (len(token) >= 3 or token.casefold() in {"ai", "ki"})
            ]
            if tokens:
                phrase_words.append(tokens)
                words.extend(tokens)

    queries = list(phrases[:MAX_SEARCH_QUERIES])
    for tokens in phrase_words:
        if len(tokens) < 2:
            continue
        pair = " ".join(tokens[:2])
        if pair not in queries:
            queries.append(pair)
            if len(queries) >= MAX_SEARCH_QUERIES:
                return queries

    for word in words:
        if word not in queries:
            queries.append(word)
            if len(queries) >= MAX_SEARCH_QUERIES:
                break

    return queries


def _commons_api(params):
    base = "https://commons.wikimedia.org/w/api.php"
    response = requests.get(base, params=params, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Wikimedia Commons returned an invalid response")
    if "error" in data:
        raise ValueError(f"Wikimedia Commons API error: {data['error']}")
    return data


def _clean_license(meta):
    lic = meta.get("LicenseShortName", {}).get("value", "")
    if not lic:
        lic = "Unbekannt"
    return str(lic)


def _clean_artist(meta):
    artist = meta.get("Artist", {}).get("value", "")
    artist = re.sub(r"<[^>]+>", "", artist)
    return artist.strip()[:60]


def search_commons(query, filetype="bitmap"):
    gsr = f"{query} filetype:{'video' if filetype == 'video' else 'bitmap'}"
    data = _commons_api(
        {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": gsr,
            "gsrnamespace": 6,
            "gsrlimit": LIMIT_PER_QUERY,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata|mime|size",
            "iiurlwidth": 480,
        }
    )
    out = []
    for page in (data.get("query", {}).get("pages", {}) or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        mime = info.get("mime", "")
        is_video = "video" in mime
        out.append(
            {
                "id": f"commons-{page.get('pageid')}",
                "title": page.get("title", "").replace("Datei:", "").replace("File:", ""),
                "thumb": info.get("thumburl", "") if not is_video else info.get("thumburl", ""),
                "url": info.get("url", ""),
                "license": _clean_license(meta),
                "artist": _clean_artist(meta),
                "source": "Wikimedia Commons",
                "type": "video" if is_video else "image",
                "mime": mime,
            }
        )
    return [m for m in out if m["url"]]


def search_openverse(query, media_type="image"):
    endpoint = "images" if media_type == "image" else "audio"
    url = (
        f"https://api.openverse.org/v1/{endpoint}/?q={quote_plus(query)}"
        f"&license_type=commercial&page_size={LIMIT_PER_QUERY}"
    )
    response = requests.get(url, headers=HEADERS, timeout=OPENVERSE_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("Openverse returned an invalid response")

    out = []
    for res in data.get("results", []):
        lic = res.get("license", "") or ""
        ver = res.get("license_version", "") or ""
        lic_str = (lic.upper() + " " + ver).strip() or "CC"
        out.append(
            {
                "id": f"openverse-{res.get('id', '')}",
                "title": res.get("title") or query,
                "thumb": res.get("thumbnail", ""),
                "url": res.get("url", ""),
                "license": lic_str,
                "artist": res.get("creator") or "unbekannt",
                "source": "Openverse",
                "type": media_type,
                "mime": "audio" if media_type == "audio" else "image",
            }
        )
    return [m for m in out if m["url"]]


def search_batch(keywords, image_queries=None, video_queries=None, audio_queries=None, topic=None):
    image_queries = _build_search_queries(keywords, image_queries, topic)
    video_queries = _build_search_queries(keywords, video_queries, topic)
    audio_queries = _build_search_queries(audio_queries)

    tasks = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for index, kw in enumerate(image_queries):
            if index < MAX_OPENVERSE_QUERIES:
                tasks.append(("Openverse", kw, ex.submit(search_openverse, kw, "image")))
            tasks.append(("Wikimedia Commons", kw, ex.submit(search_commons, kw, "bitmap")))
        for kw in video_queries:
            tasks.append(("Wikimedia Commons", kw, ex.submit(search_commons, kw, "video")))
        for kw in audio_queries:
            tasks.append(("Openverse", kw, ex.submit(search_openverse, kw, "audio")))

        results = []
        successful_requests = 0
        failures = []
        for provider, query, future in tasks:
            try:
                results.append(future.result())
                successful_requests += 1
            except (requests.RequestException, ValueError) as exc:
                logger.warning("%s media search failed for %r: %s", provider, query, exc)
                failures.append(exc)

    if tasks and not successful_requests:
        raise MediaSearchError("All media providers failed") from failures[0]

    flat = [media for result in results for media in result]
    seen, media = set(), []
    for m in flat:
        if m["url"] in seen:
            continue
        seen.add(m["url"])
        media.append(m)
    return media[:MAX_RESULTS]
