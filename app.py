"""
Dexter.pw Nuvio addon v1.1.0
- Movie + Series
- Lấy HLS từ meta.sources (kind=hls), không hardcode site id
- URL playlist/segment trỏ thẳng dexter.pw (không stream video qua container)
"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

VERSION = "1.1.0"
DEXTER = os.getenv("DEXTER_BASE", "https://dexter.pw").rstrip("/")
TMDB_KEY = os.getenv("TMDB_API_KEY", "1adf1a2b5aece0ac5106302d3299f56f")
MIN_HEIGHT = int(os.getenv("MIN_HEIGHT", "1080"))
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Referer": f"{DEXTER}/",
    "Accept": "application/json, text/plain, */*",
}

app = FastAPI(title="Dexter Addon")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MANIFEST = {
    "id": "org.nuvio.dexter.pw",
    "version": VERSION,
    "name": "Dexter",
    "description": "dexter.pw · HLS ≥1080p / 4K · movie + series",
    "logo": "https://dexter.pw/favicon.svg",
    "resources": ["stream"],
    "types": ["movie", "series"],
    "idPrefixes": ["tt", "tmdb"],
    "catalogs": [],
}


def cors_json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(
        data, status_code=status, headers={"Access-Control-Allow-Origin": "*"}
    )


@app.get("/")
@app.get("/manifest.json")
async def manifest():
    return cors_json(MANIFEST)


@app.get("/health")
async def health():
    return cors_json({"ok": True, "version": VERSION})


async def http_get(url: str, accept: str | None = None) -> tuple[int, str]:
    h = dict(HEADERS)
    if accept:
        h["Accept"] = accept
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.get(url, headers=h)
        return r.status_code, r.text


async def tmdb_find(imdb_id: str, want: str) -> int | None:
    """want: movie | tv"""
    url = (
        f"https://api.themoviedb.org/3/find/{imdb_id}"
        f"?api_key={TMDB_KEY}&external_source=imdb_id"
    )
    try:
        code, text = await http_get(url)
        if code != 200:
            return None
        import json

        data = json.loads(text)
        key = "movie_results" if want == "movie" else "tv_results"
        results = data.get(key) or []
        if results:
            return int(results[0]["id"])
    except Exception:
        return None
    return None


def parse_movie_id(raw: str) -> tuple[str | None, int | None]:
    raw = raw.replace(".json", "").strip()
    if raw.startswith("tmdb:"):
        try:
            return None, int(raw.split(":", 1)[1])
        except ValueError:
            return None, None
    if raw.startswith("tt"):
        return (raw.split(":")[0] if ":" in raw else raw), None
    if raw.isdigit():
        return None, int(raw)
    return None, None


def parse_series_id(raw: str) -> tuple[str | None, int | None, int, int]:
    """
    tt123:1:2 | tmdb:1405:1:2 | 1405:1:2
    → imdb, tmdb, season, episode
    """
    raw = raw.replace(".json", "").strip()
    parts = raw.split(":")
    season, episode = 1, 1
    imdb, tmdb = None, None

    if parts[0] == "tmdb" and len(parts) >= 2:
        try:
            tmdb = int(parts[1])
        except ValueError:
            tmdb = None
        if len(parts) >= 4:
            season, episode = int(parts[2]), int(parts[3])
        elif len(parts) == 3:
            season, episode = int(parts[2]), 1
    elif parts[0].startswith("tt"):
        imdb = parts[0]
        if len(parts) >= 3:
            season, episode = int(parts[1]), int(parts[2])
    elif parts[0].isdigit():
        tmdb = int(parts[0])
        if len(parts) >= 3:
            season, episode = int(parts[1]), int(parts[2])
    return imdb, tmdb, season, episode


_STREAM_INF = re.compile(
    r"#EXT-X-STREAM-INF:([^\n]+)\n([^\s\n]+)",
    re.MULTILINE,
)
_RES = re.compile(r"RESOLUTION=(\d+)x(\d+)", re.I)
_BW = re.compile(r"BANDWIDTH=(\d+)", re.I)


def abs_url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return f"{DEXTER}{path}"


def parse_master(m3u8: str) -> list[dict]:
    out = []
    for m in _STREAM_INF.finditer(m3u8):
        info, path = m.group(1), m.group(2).strip()
        rm = _RES.search(info)
        bm = _BW.search(info)
        if not rm:
            continue
        w, h = int(rm.group(1)), int(rm.group(2))
        bw = int(bm.group(1)) if bm else 0
        out.append(
            {
                "width": w,
                "height": h,
                "bandwidth": bw,
                "url": abs_url(path),
            }
        )
    out.sort(key=lambda x: (x["height"], x["bandwidth"]), reverse=True)
    return out


def quality_label(h: int) -> str:
    if h >= 2160:
        return "4K"
    if h >= 1440:
        return "1440p"
    if h >= 1080:
        return "FHD"
    if h >= 720:
        return "HD"
    return f"{h}p"


async def meta_sources_movie(tmdb_id: int) -> list[str]:
    import json

    code, text = await http_get(f"{DEXTER}/api/movie/{tmdb_id}?site=1")
    if code != 200:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    urls = []
    for s in data.get("sources") or []:
        if s.get("kind") == "hls" and s.get("url"):
            urls.append(abs_url(s["url"]))
    return urls


async def meta_sources_episode(tmdb_id: int, season: int, episode: int) -> list[str]:
    import json

    code, text = await http_get(
        f"{DEXTER}/api/tv/{tmdb_id}/episode/{season}/{episode}?site=1"
    )
    if code != 200:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    urls = []
    for s in data.get("sources") or []:
        if s.get("kind") == "hls" and s.get("url"):
            urls.append(abs_url(s["url"]))
    return urls


async def masters_to_streams(
    master_urls: list[str], binge: str, ep_label: str = ""
) -> list[dict]:
    streams: list[dict] = []
    seen: set[str] = set()
    for mu in master_urls:
        code, body = await http_get(mu, accept="application/vnd.apple.mpegurl,*/*")
        if code != 200 or not body.strip().startswith("#EXTM3U"):
            continue
        variants = [v for v in parse_master(body) if v["height"] >= MIN_HEIGHT]
        if not variants:
            variants = parse_master(body)[:1]
        for v in variants:
            if v["url"] in seen:
                continue
            seen.add(v["url"])
            q = quality_label(v["height"])
            title_lines = [f"{q} · {v['width']}x{v['height']}"]
            if ep_label:
                title_lines.insert(0, ep_label)
            title_lines.append("Dexter · HLS")
            streams.append(
                {
                    "name": f"Dexter {q}",
                    "title": "\n".join(title_lines),
                    "url": v["url"],
                    "behaviorHints": {
                        "notWebReady": True,
                        "bingeGroup": binge,
                        "proxyHeaders": {
                            "request": {
                                "Referer": f"{DEXTER}/",
                                "User-Agent": UA,
                                "Origin": DEXTER,
                            }
                        },
                    },
                }
            )
    return streams


@app.get("/stream/movie/{id_raw}")
async def stream_movie(id_raw: str):
    imdb, tmdb = parse_movie_id(id_raw)
    if tmdb is None and imdb:
        tmdb = await tmdb_find(imdb, "movie")
    if not tmdb:
        return cors_json({"streams": []})

    masters = await meta_sources_movie(tmdb)
    if not masters:
        # fallback site paths hay gặp
        masters = [
            f"{DEXTER}/api/s/1/movie/{tmdb}",
            f"{DEXTER}/api/s/21/movie/{tmdb}?v=3",
        ]
    streams = await masters_to_streams(masters, binge=f"dexter-movie-{tmdb}")
    return cors_json({"streams": streams})


@app.get("/stream/series/{id_raw}")
async def stream_series(id_raw: str):
    imdb, tmdb, season, episode = parse_series_id(id_raw)
    if tmdb is None and imdb:
        tmdb = await tmdb_find(imdb, "tv")
    if not tmdb:
        return cors_json({"streams": []})

    masters = await meta_sources_episode(tmdb, season, episode)
    if not masters:
        masters = [
            f"{DEXTER}/api/s/20/tv/{tmdb}/{season}/{episode}",
            f"{DEXTER}/api/s/1/tv/{tmdb}/{season}/{episode}",
        ]
    ep_label = f"Tập {episode}" if episode else ""
    streams = await masters_to_streams(
        masters,
        binge=f"dexter-tv-{tmdb}-s{season}",
        ep_label=ep_label,
    )
    return cors_json({"streams": streams})
