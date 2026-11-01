import re
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor

import requests

HEADERS = {
    "User-Agent": "PostCreator/1.0 (lokales Tool fuer lizenzfreie Medien)",
}
TIMEOUT = 15
LIMIT_PER_QUERY = 8
MAX_RESULTS = 32


def _commons_api(params):
    base = "https://commons.wikimedia.org/w/api.php"
    return requests.get(base, params=params, headers=HEADERS, timeout=TIMEOUT).json()


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
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    except Exception:
        return []
    if r.status_code != 200:
        return []
    out = []
    for res in r.json().get("results", []):
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
    image_queries = set(image_queries or [])
    video_queries = set(video_queries or [])
    audio_queries = set(audio_queries or [])
    if topic:
        image_queries.add(topic)
        video_queries.add(topic)
    tasks = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for kw in image_queries:
            tasks.append(ex.submit(search_openverse, kw, "image"))
            tasks.append(ex.submit(search_commons, kw, "bitmap"))
        for kw in video_queries:
            tasks.append(ex.submit(search_commons, kw, "video"))
        for kw in audio_queries:
            tasks.append(ex.submit(search_openverse, kw, "audio"))
        results = [f.result() for f in tasks]
    flat = [m for r in results for m in r]
    seen, media = set(), []
    for m in flat:
        if m["url"] in seen:
            continue
        seen.add(m["url"])
        media.append(m)
    return media[:MAX_RESULTS]
