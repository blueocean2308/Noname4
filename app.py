"""
Dexter.pw Nuvio addon — Docker-ready
Stream: GET https://dexter.pw/api/s/1/movie/{tmdbId} → HLS master (4K/1080/…)
No Turnstile on /api/s path. Playlist URLs stay on dexter.pw (no video through VPS).
"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

VERSION = "1.0.0"
PORT = int(os.getenv("PORT", "51825"))
DEXTER = os.getenv("DEXTER_BASE", "https://dexter.pw").rstrip("/")
SITE_ID = os.getenv("DEXTER_SITE", "1")
TMDB_KEY = os.getenv("TMDB_API_KEY", "1adf1a2b5aece0ac5106302d3299f56f")
MIN_HEIGHT = int(os.getenv("MIN_HEIGHT", "1080"))
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Referer": f"{DEXTER}/",
    "Accept": "*/*",
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
    "description": "dexter.pw · HLS ≥1080p / 4K (movies)",
    "logo": "https://dexter.pw/favicon.svg",
    "resources": ["stream"],
    "types": ["movie"],
    "idPrefixes": ["tt", "tmdb"],
    "catalogs": [],
}


def cors_json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(
        data,
        status_code=status,
        headers={"Access-Control-Allow-Origin": "*"},
    )


@app.get("/")
@app.get("/manifest.json")
async def manifest():
    return cors_json(MANIFEST)


@app.get("/health")
async def health():
    return cors_json({"ok": True, "version": VERSION})


async def tmdb_from_imdb(imdb_id: str) -> int | None:
    """tt1234567 → tmdb movie id"""
    url = (
        f"https://api.themoviedb.org/3/find/{imdb_id}"
        f"?api_key={TMDB_KEY}&external_source=imdb_id"
    )
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            data = r.json()
            results = data.get("movie_results") or []
            if results:
                return int(results[0]["id"])
    except Exception:
        return None
    return None


def parse_id(raw: str) -> tuple[str, str | None, int | None]:
    """
    Returns (kind, imdb_or_none, tmdb_or_none)
    kind: movie
    """
    raw = raw.replace(".json", "").strip()
    if raw.startswith("tmdb:"):
        try:
            return "movie", None, int(raw.split(":", 1)[1])
        except ValueError:
            return "movie", None, None
    if raw.startswith("tt"):
        return "movie", raw.split(":")[0] if ":" in raw else raw, None
    # bare number → tmdb
    if raw.isdigit():
        return "movie", None, int(raw)
    return "movie", None, None


_STREAM_INF = re.compile(
    r"#EXT-X-STREAM-INF:([^\n]+)\n(/api/v/[^\s\n]+)",
    re.MULTILINE,
)
_RES = re.compile(r"RESOLUTION=(\d+)x(\d+)", re.I)
_BW = re.compile(r"BANDWIDTH=(\d+)", re.I)


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
                "path": path,
                "url": f"{DEXTER}{path}" if path.startswith("/") else path,
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


async def fetch_master(tmdb_id: int) -> str | None:
    url = f"{DEXTER}/api/s/{SITE_ID}/movie/{tmdb_id}"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get(url, headers=HEADERS)
            if r.status_code != 200:
                return None
            text = r.text.strip()
            if not text.startswith("#EXTM3U"):
                return None
            return text
    except Exception:
        return None


@app.get("/stream/movie/{id_raw}")
async def stream_movie(id_raw: str):
    _, imdb, tmdb = parse_id(id_raw)
    if tmdb is None and imdb:
        tmdb = await tmdb_from_imdb(imdb)
    if not tmdb:
        return cors_json({"streams": []})

    master = await fetch_master(tmdb)
    if not master:
        return cors_json({"streams": []})

    variants = [v for v in parse_master(master) if v["height"] >= MIN_HEIGHT]
    if not variants:
        # fallback: keep highest available even if < MIN
        variants = parse_master(master)[:1]

    streams = []
    for v in variants:
        q = quality_label(v["height"])
        title = f"{q} · {v['width']}x{v['height']}\nDexter · HLS"
        streams.append(
            {
                "name": f"Dexter {q}",
                "title": title,
                "url": v["url"],
                "behaviorHints": {
                    "notWebReady": True,
                    "bingeGroup": f"dexter-{tmdb}",
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
    return cors_json({"streams": streams})


@app.get("/stream/series/{id_raw}")
async def stream_series(id_raw: str):
    # Site kho series hạn chế — trả rỗng để khỏi spam lỗi
    return cors_json({"streams": []})


@app.api_route("/{path:path}", methods=["GET", "OPTIONS"])
async def fallback(path: str, request: Request):
    if request.method == "OPTIONS":
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
            },
        )
    return cors_json({"error": "not found"}, 404)
