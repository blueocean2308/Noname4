"""
Dexter.pw Nuvio addon v1.2.0
stream + catalog + meta (movie & series)
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

VERSION = "1.2.0"
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
IMG = "https://image.tmdb.org/t/p/w500"

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
    "description": "dexter.pw · catalog + HLS ≥1080p / 4K",
    "logo": "https://dexter.pw/favicon.svg",
    "resources": ["catalog", "meta", "stream"],
    "types": ["movie", "series"],
    "idPrefixes": ["tt", "tmdb"],
    "catalogs": [
        {
            "type": "movie",
            "id": "dexter_movies",
            "name": "Dexter Movies",
            "extra": [{"name": "search", "isRequired": False}, {"name": "skip"}],
        },
        {
            "type": "series",
            "id": "dexter_series",
            "name": "Dexter Series",
            "extra": [{"name": "search", "isRequired": False}, {"name": "skip"}],
        },
    ],
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


async def http_json(url: str) -> Any | None:
    code, text = await http_get(url)
    if code != 200:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def poster_url(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith("http"):
        return path
    return f"{IMG}{path}"


def meta_item(it: dict) -> dict:
    t = it.get("type") or "movie"
    stremio_type = "series" if t == "tv" else "movie"
    tid = it.get("id")
    return {
        "id": f"tmdb:{tid}",
        "type": stremio_type,
        "name": it.get("title") or "",
        "poster": poster_url(it.get("poster")),
        "background": poster_url(it.get("backdrop")) or poster_url(it.get("poster")),
        "releaseInfo": str(it.get("year") or ""),
        "imdbRating": str(it.get("rating")) if it.get("rating") else None,
    }


async def tmdb_find(imdb_id: str, want: str) -> int | None:
    url = (
        f"https://api.themoviedb.org/3/find/{imdb_id}"
        f"?api_key={TMDB_KEY}&external_source=imdb_id"
    )
    data = await http_json(url)
    if not data:
        return None
    key = "movie_results" if want == "movie" else "tv_results"
    results = data.get(key) or []
    return int(results[0]["id"]) if results else None


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
    elif parts[0].startswith("tt"):
        imdb = parts[0]
        if len(parts) >= 3:
            season, episode = int(parts[1]), int(parts[2])
    elif parts[0].isdigit():
        tmdb = int(parts[0])
        if len(parts) >= 3:
            season, episode = int(parts[1]), int(parts[2])
    return imdb, tmdb, season, episode


# ── catalog ─────────────────────────────────────────────

@app.get("/catalog/{type}/{catalog_id}.json")
@app.get("/catalog/{type}/{catalog_id}/skip={skip}.json")
@app.get("/catalog/{type}/{catalog_id}/search={query}.json")
async def catalog(
    type: str, catalog_id: str, skip: int = 0, query: str | None = None
):
    page = max(1, skip // 20 + 1)
    if query:
        from urllib.parse import quote
        data = await http_json(f"{DEXTER}/api/search?q={quote(query)}")
        results = (data or {}).get("results") or []
        want = "tv" if type == "series" else "movie"
        metas = [meta_item(x) for x in results if x.get("type") == want]
        return cors_json({"metas": metas})

    want = "tv" if type == "series" else "movie"
    data = await http_json(f"{DEXTER}/api/browse?page={page}&type={want}")
    results = (data or {}).get("results") or []
    metas = [meta_item(x) for x in results]
    return cors_json({"metas": metas})


# ── meta ────────────────────────────────────────────────

@app.get("/meta/movie/{id_raw}")
async def meta_movie(id_raw: str):
    imdb, tmdb = parse_movie_id(id_raw)
    if tmdb is None and imdb:
        tmdb = await tmdb_find(imdb, "movie")
    if not tmdb:
        return cors_json({"meta": {}})
    data = await http_json(f"{DEXTER}/api/movie/{tmdb}")
    if not data:
        return cors_json({"meta": {}})
    meta = {
        "id": f"tmdb:{tmdb}",
        "type": "movie",
        "name": data.get("title") or "",
        "description": data.get("overview") or "",
        "poster": poster_url(data.get("poster")),
        "background": poster_url(data.get("backdrop")),
        "releaseInfo": str(data.get("year") or ""),
        "imdbRating": str(data.get("rating")) if data.get("rating") else None,
        "genres": data.get("genres") or data.get("genreList") or [],
        "runtime": f"{data['runtime']} min" if data.get("runtime") else None,
    }
    if data.get("imdbId"):
        meta["imdb_id"] = data["imdbId"]
    return cors_json({"meta": meta})


@app.get("/meta/series/{id_raw}")
async def meta_series(id_raw: str):
    raw = id_raw.replace(".json", "")
    # strip episode suffix if any
    if raw.startswith("tmdb:"):
        parts = raw.split(":")
        try:
            tmdb = int(parts[1])
        except (IndexError, ValueError):
            return cors_json({"meta": {}})
    elif raw.startswith("tt"):
        imdb = raw.split(":")[0]
        tmdb = await tmdb_find(imdb, "tv")
        if not tmdb:
            return cors_json({"meta": {}})
    elif raw.isdigit():
        tmdb = int(raw)
    else:
        return cors_json({"meta": {}})

    data = await http_json(f"{DEXTER}/api/tv/{tmdb}")
    if not data:
        return cors_json({"meta": {}})

    videos = []
    for s in data.get("seasonList") or []:
        sn = int(s.get("number") or 0)
        if sn < 1:
            continue
        season = await http_json(f"{DEXTER}/api/tv/{tmdb}/season/{sn}")
        for ep in (season or {}).get("episodes") or []:
            e_num = int(ep.get("episode") or 0)
            videos.append(
                {
                    "id": f"tmdb:{tmdb}:{sn}:{e_num}",
                    "title": ep.get("title") or f"Tập {e_num}",
                    "season": sn,
                    "episode": e_num,
                    "overview": ep.get("overview") or "",
                    "thumbnail": poster_url(ep.get("still")),
                    "released": ep.get("date") or None,
                    "available": bool(ep.get("playable", True)),
                }
            )

    meta = {
        "id": f"tmdb:{tmdb}",
        "type": "series",
        "name": data.get("title") or "",
        "description": data.get("overview") or "",
        "poster": poster_url(data.get("poster")),
        "background": poster_url(data.get("backdrop")),
        "releaseInfo": str(data.get("year") or data.get("firstAired") or ""),
        "imdbRating": str(data.get("rating")) if data.get("rating") else None,
        "genres": data.get("genres") or data.get("genreList") or [],
        "videos": videos,
    }
    if data.get("imdbId"):
        meta["imdb_id"] = data["imdbId"]
    return cors_json({"meta": meta})


# ── stream ──────────────────────────────────────────────

_STREAM_INF = re.compile(
    r"#EXT-X-STREAM-INF:([^\n]+)\n([^\s\n]+)", re.MULTILINE
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
        rm, bm = _RES.search(info), _BW.search(info)
        if not rm:
            continue
        w, h = int(rm.group(1)), int(rm.group(2))
        bw = int(bm.group(1)) if bm else 0
        out.append({"width": w, "height": h, "bandwidth": bw, "url": abs_url(path)})
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
    data = await http_json(f"{DEXTER}/api/movie/{tmdb_id}")
    if not data:
        return []
    return [
        abs_url(s["url"])
        for s in (data.get("sources") or [])
        if s.get("kind") == "hls" and s.get("url")
    ]


async def meta_sources_episode(tmdb_id: int, season: int, episode: int) -> list[str]:
    data = await http_json(
        f"{DEXTER}/api/tv/{tmdb_id}/episode/{season}/{episode}"
    )
    if not data:
        return []
    return [
        abs_url(s["url"])
        for s in (data.get("sources") or [])
        if s.get("kind") == "hls" and s.get("url")
    ]


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
            lines = [f"{q} · {v['width']}x{v['height']}"]
            if ep_label:
                lines.insert(0, ep_label)
            lines.append("Dexter · HLS")
            streams.append(
                {
                    "name": f"Dexter {q}",
                    "title": "\n".join(lines),
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
        masters = [
            f"{DEXTER}/api/s/1/movie/{tmdb}",
            f"{DEXTER}/api/s/21/movie/{tmdb}?v=3",
        ]
    return cors_json(
        {"streams": await masters_to_streams(masters, f"dexter-movie-{tmdb}")}
    )


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
    return cors_json(
        {
            "streams": await masters_to_streams(
                masters,
                f"dexter-tv-{tmdb}-s{season}",
                ep_label=f"Tập {episode}",
            )
        }
    )
