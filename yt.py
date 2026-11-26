from http.client import HTTPException

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

LIMIT = 50 * 1024 * 1024
DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp/duu-data")).resolve()
YOUTUBE_CACHE_DIR = Path(os.getenv("CACHE_DIR") or DATA_DIR / ".youtube-cache").resolve()
YOUTUBE_CACHE_TTL = int(os.getenv("CACHE_TTL", "21600"))
YOUTUBE_CACHE_LIMIT = int(os.getenv("CACHE_MAX_BYTES", "268435456"))
YOUTUBE_AUDIO_FORMATS = (".m4a", ".webm", ".mp4")

def resolve(link):
    if len(link) > 2048:
        return None
    link = link.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", link):
        return link
    shared = re.search(r"https?://[^\s<>]+", link, re.IGNORECASE)
    if shared:
        link = shared[0].rstrip(").,]")
    elif "://" not in link:
        link = "https://" + link
    try:
        url = urlsplit(link)
        if url.scheme not in {"http", "https"} or url.username or url.password or url.port:
            return None
    except ValueError:
        return None
    if url.hostname == "youtu.be":
        video_id = url.path.strip("/")
    elif url.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
                         "youtube-nocookie.com", "www.youtube-nocookie.com"}:
        if url.path == "/watch":
            video_id = parse_qs(url.query).get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:shorts|live|embed|v)/([^/]+)/?", url.path)
            video_id = match[1] if match else ""
    else:
        return None
    return video_id if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) else None


class YouTubeImportError(Exception):
    pass


class YouTubeBlocked(YouTubeImportError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _download_apify(video_id, folder, token):
    opener = build_opener(NoRedirect)
    request = Request(
        "https://api.apify.com/v2/acts/g2h290TFYTSCWID7f/run-sync-get-dataset-items"
        "?timeout=60&maxItems=1&maxTotalChargeUsd=0.15&limit=2",
        data=json.dumps({
            "format": "m4a",
            "urls": [f"https://www.youtube.com/watch?v={video_id}"],
        }).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with opener.open(request, timeout=75) as response:
        items = json.loads(response.read(65536))
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise ValueError("unexpected Apify dataset")
    item = items[0]
    url = item.get("downloadUrl")
    if (item.get("status") != "completed" or item.get("videoId") != video_id
            or not isinstance(url, str)
            or not re.fullmatch(r"https://apify\.dziura\.online/temp/[a-fA-F0-9]{64}\.m4a", url)):
        raise ValueError("unexpected Apify download result")

    with tempfile.TemporaryDirectory(dir=folder) as scratch:
        source = Path(scratch) / "audio.m4a"
        remaining = LIMIT
        deadline = time.monotonic() + 35
        with opener.open(url, timeout=12) as response, source.open("wb") as target:
            while chunk := response.read1(65536):
                remaining -= len(chunk)
                if remaining < 0 or time.monotonic() > deadline:
                    raise ValueError("Apify audio exceeded transfer limits")
                target.write(chunk)
        with source.open("rb") as audio:
            if audio.read(8)[4:8] != b"ftyp":
                raise ValueError("invalid Apify audio container")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-protocol_whitelist", "file",
             "-select_streams", "a:0", "-show_entries", "stream=index:format=duration",
             "-of", "json", str(source)],
            capture_output=True, check=True, timeout=8,
        )
        metadata = json.loads(probe.stdout)
        if not metadata.get("streams") or not 0 < float(metadata["format"]["duration"]) <= 600:
            raise ValueError("Apify audio outside duration limits")
        return source.replace(folder / "apify.m4a")


def _download_with_fallback(video_id, folder):
    try:
        return _download(video_id, folder)
    except YouTubeBlocked:
        token = os.getenv("APIFY_API_TOKEN", "").strip()
        if token:
            try:
                source = _download_apify(video_id, folder, token)
                logging.info("Apify audio import succeeded for %s", video_id)
                return source
            except HTTPError as error:
                logging.warning("Apify audio import HTTP %s for %s", error.code, video_id)
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError,
                    HTTPException) as error:
                logging.warning("Apify audio import failed for %s (%s)", video_id, type(error).__name__)
        raise


def prune_youtube_cache():
    now = time.time()
    entries = []
    for path in YOUTUBE_CACHE_DIR.iterdir():
        if not path.is_file():
            continue
        info = path.stat()
        ttl = 600 if path.suffix == ".fail" else YOUTUBE_CACHE_TTL
        if now - info.st_mtime >= ttl:
            path.unlink(missing_ok=True)
        else:
            entries.append((info.st_atime, info.st_size, path))
    size = sum(entry[1] for entry in entries)
    for _, length, path in sorted(entries):
        if size <= YOUTUBE_CACHE_LIMIT:
            break
        path.unlink(missing_ok=True)
        size -= length


def read_youtube_cache(video_id, folder, extensions=YOUTUBE_AUDIO_FORMATS):
    if not YOUTUBE_CACHE_DIR.is_dir():
        return None
    try:
        prune_youtube_cache()
        for extension in extensions:
            cached = YOUTUBE_CACHE_DIR / f"{video_id}{extension}"
            if cached.is_file() and 0 < cached.stat().st_size <= LIMIT:
                destination = folder / f"cached{extension}"
                shutil.copy2(cached, destination)
                os.utime(cached, (time.time(), cached.stat().st_mtime))
                logging.info("YouTube cache HIT %s", video_id)
                return destination
    except OSError:
        logging.warning("YouTube cache read failed", exc_info=True)
    return None


def store_youtube_cache(video_id, source):
    temporary = None
    try:
        YOUTUBE_CACHE_DIR.mkdir(mode=0o700, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=YOUTUBE_CACHE_DIR, delete=False) as target:
            temporary = Path(target.name)
            with source.open("rb") as audio:
                shutil.copyfileobj(audio, target)
        temporary.replace(YOUTUBE_CACHE_DIR / f"{video_id}{source.suffix}")
        prune_youtube_cache()
    except OSError:
        logging.warning("YouTube cache write failed", exc_info=True)
        if temporary:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _download(video_id, folder):
    completed = subprocess.run([sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--sleep-requests", "1",
         "--js-runtimes", "node", "--socket-timeout", "15", "--retries", "1",
         "--match-filter", "!is_live & duration <= 600",
         "--format", "bestaudio[ext=m4a]/bestaudio[ext=webm]/best[protocol=https][ext=mp4][height<=360]",
         "--max-filesize", "50M", "--output", str(folder / "youtube.%(ext)s"),
         "--", f"https://www.youtube.com/watch?v={video_id}"], timeout=150,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors="replace",
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode:
        detail = completed.stderr.lower()
        logging.warning("YouTube import failed for %s: %s", video_id, completed.stderr[-1200:])
        if any(reason in detail for reason in ("not a bot", "not a robot", "http error 403")):
            raise YouTubeBlocked("YouTube серверээс аудио авахыг түр хязгаарлалаа. MP3 эсвэл MP4 файлаа оруулж үргэлжлүүлээрэй.")
        if any(reason in detail for reason in ("unavailable", "private video", "age-restricted")):
            raise YouTubeImportError("Энэ бичлэгийг YouTube-ээс авах боломжгүй байна. Өөр нээлттэй линк эсвэл MP3/MP4 файл оруулна уу.")
        raise YouTubeImportError("YouTube-ээс аудио татаж чадсангүй. Өөр линк эсвэл MP3/MP4 файл оруулна уу.")
    for extension in YOUTUBE_AUDIO_FORMATS:
        source = folder / f"youtube{extension}"
        if source.is_file() and 0 < source.stat().st_size <= LIMIT:
            return source
    raise YouTubeImportError("Бичлэг 10 минут эсвэл 50 МБ-ын хязгаарт багтаагүй, эсвэл татах боломжтой аудио олдсонгүй. MP3/MP4 файл оруулж үзээрэй.")


def download_youtube(video_id, folder):
    marker = YOUTUBE_CACHE_DIR / f"{video_id}.fail"
    try:
        recent = time.time() - marker.stat().st_mtime < 600
    except OSError:
        recent = False
    if recent:
        raise YouTubeImportError("Өмнөх таталт амжилтгүй болсон. 10 минутын дараа дахин оролдох эсвэл файл оруулна уу.")
    logging.info("YouTube cache MISS %s", video_id)
    try:
        return _download_with_fallback(video_id, folder)
    except (YouTubeImportError, subprocess.TimeoutExpired) as error:
        try:
            YOUTUBE_CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            marker.touch()
            prune_youtube_cache()
        except OSError:
            logging.warning("Could not store YouTube failure marker", exc_info=True)
        if isinstance(error, subprocess.TimeoutExpired):
            raise YouTubeImportError("YouTube таталтын хугацаа дууслаа. Файл оруулж үргэлжлүүлнэ үү.") from error
        raise
