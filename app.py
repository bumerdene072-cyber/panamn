import json
import logging
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
from threading import BoundedSemaphore, Lock, Thread
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from flask import Flask, jsonify, request, send_file

import wire

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 51 * 1024 * 1024
LIMIT = 50 * 1024 * 1024
RETENTION = 24 * 3600
PAID_RETENTION = 7 * 24 * 3600
DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp/duu-data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
FORMATS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4"}
STEMS = {"vocals": "vocals", "instrumental": "no_vocals"}
lock = Lock()
payment_lock = Lock()
slots = BoundedSemaphore(1)


@app.get("/")
def home():
    return app.send_static_file("index.html")


@app.get("/sw.js")
def service_worker():
    response = app.send_static_file("sw.js")
    response.cache_control.no_cache = True
    return response


@app.after_request
def prevent_job_caching(response):
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.path.startswith("/api/"):
        response.cache_control.no_store = True
    return response


@app.errorhandler(413)
def oversized(_):
    return jsonify(error="Файлын хэмжээ 50 МБ-аас хэтэрсэн байна."), 413


def read_job(job_id):
    try:
        if UUID(job_id).hex != job_id:
            return None
        job = json.loads((DATA_DIR / job_id / "job.json").read_text())
    except (ValueError, FileNotFoundError):
        return None
    return job if job["expires"] > time.time() else None


def write_job(job_id, job):
    folder = DATA_DIR / job_id
    temporary = folder / "job.tmp"
    temporary.write_text(json.dumps(job))
    temporary.replace(folder / "job.json")


def remove_expired():
    for metadata in DATA_DIR.glob("*/job.json"):
        if not read_job(metadata.parent.name):
            shutil.rmtree(metadata.parent, ignore_errors=True)


def run(command, timeout):
    return subprocess.run(
        command, check=True, timeout=timeout, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )


def youtube_video_id(link):
    if len(link) > 2048:
        return None
    try:
        url = urlsplit(link.strip())
        if url.scheme not in {"http", "https"} or url.username or url.password or url.port:
            return None
    except ValueError:
        return None
    if url.hostname == "youtu.be":
        video_id = url.path.strip("/")
    elif url.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if url.path == "/watch":
            video_id = parse_qs(url.query).get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:shorts|live|embed)/([^/]+)/?", url.path)
            video_id = match[1] if match else ""
    else:
        return None
    return video_id if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) else None


class YouTubeImportError(Exception):
    pass


def download_youtube(video_id, folder):
    completed = subprocess.run([sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist",
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
        if "not a bot" in detail or "not a robot" in detail:
            raise YouTubeImportError("YouTube серверээс аудио авахыг түр хязгаарлалаа. MP3 эсвэл MP4 файлаа оруулж үргэлжлүүлээрэй.")
        if any(reason in detail for reason in ("unavailable", "private video", "age-restricted")):
            raise YouTubeImportError("Энэ бичлэгийг YouTube-ээс авах боломжгүй байна. Өөр нээлттэй линк эсвэл MP3/MP4 файл оруулна уу.")
        raise YouTubeImportError("YouTube-ээс аудио татаж чадсангүй. Өөр линк эсвэл MP3/MP4 файл оруулна уу.")
    for ext in ("m4a", "webm", "mp4"):
        source = folder / f"youtube.{ext}"
        if source.is_file() and 0 < source.stat().st_size <= LIMIT:
            return source
    raise YouTubeImportError("Бичлэг 10 минут эсвэл 50 МБ-ын хязгаарт багтаагүй, эсвэл татах боломжтой аудио олдсонгүй. MP3/MP4 файл оруулж үзээрэй.")


def separate(job_id, folder, source, video_id):
    message = "Файлын дууг уншиж чадсангүй. Өөр файл оруулж үзээрэй."
    try:
        if video_id:
            message = (
                "YouTube-ээс аудио авч чадсангүй. Нийтэд нээлттэй, 10 минут хүртэлх "
                "бичлэгийн холбоос эсвэл аудио файл оруулна уу."
            )
            try:
                source = download_youtube(video_id, folder)
            except YouTubeImportError as error:
                message = str(error)
                raise
            with lock:
                write_job(job_id, {"status": "processing", "expires": time.time() + 3600})
            message = "YouTube-ээс авсан аудиог уншиж чадсангүй."
        track = folder / "track.wav"
        run(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file", "-format_whitelist",
             "mp3,wav,flac,ogg,mov,mp4,m4a,3gp,3g2,mj2,matroska,webm", "-i", str(source), "-map",
             "0:a:0", "-vn", "-ac", "2", "-ar", "44100", "-t", "601", str(track)], 90)
        source.unlink()
        # Stereo 16-bit PCM at 44.1 kHz, with room for the WAV header.
        if track.stat().st_size > 600 * 44100 * 4 + 1024:
            message = "10 минут хүртэлх бичлэг оруулна уу."
            raise ValueError(message)
        message = "AI боловсруулалт амжилтгүй боллоо. Дахин оролдоно уу."
        run([sys.executable, "-m", "demucs", "-n", "htdemucs", "-d",
             os.environ.get("DEMUCS_DEVICE", "cpu"), "--two-stems", "vocals",
             "--mp3", "--mp3-bitrate", "320", "-o", str(folder), str(track)], 1200)
        for name in STEMS.values():
            stem = folder / "htdemucs" / "track" / f"{name}.mp3"
            if not stem.stat().st_size:
                raise ValueError("Empty output stem")
            run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(stem), "-t", "20",
                 "-c:a", "libmp3lame", "-b:a", "128k", str(folder / f"preview-{name}.mp3")], 30)
        track.unlink()
        with lock:
            write_job(job_id, {"status": "ready", "expires": time.time() + RETENTION})
    except Exception:
        logging.exception("Separation failed for job %s", job_id)
        shutil.rmtree(folder, ignore_errors=True)
        with lock:
            folder.mkdir(mode=0o700)
            write_job(job_id, {"status": "failed", "error": message, "expires": time.time() + 3600})
    finally:
        slots.release()


@app.post("/api/jobs")
def submit():
    upload = request.files.get("audio")
    ext = Path(upload.filename or "").suffix.lower() if upload else ""
    link = request.form.get("youtube_url", "").strip()
    video_id = youtube_video_id(link) if link else None
    if link and (not video_id or upload):
        return jsonify(error="YouTube бичлэгийн зөв холбоос оруулна уу."), 400
    if not video_id and ext not in FORMATS:
        return jsonify(error="MP3, MP4, WAV, M4A, FLAC эсвэл OGG файл оруулна уу."), 400
    if not slots.acquire(blocking=False):
        return jsonify(error="Сервер дуу боловсруулж байна. Түр хүлээгээд дахин оролдоно уу."), 429
    job_id = uuid4().hex
    folder = DATA_DIR / job_id
    started = False
    try:
        if payment_lock.acquire(blocking=False):
            try:
                with lock:
                    remove_expired()
            finally:
                payment_lock.release()
        with lock:
            if sum(1 for _ in DATA_DIR.glob("*/job.json")) >= 150:
                return jsonify(error="Серверийн хадгалах зай түр дүүрсэн байна."), 503
            folder.mkdir(mode=0o700)
        source = None
        if not video_id:
            source = folder / f"source{ext}"
            upload.save(source)
            size = source.stat().st_size
            if size > LIMIT:
                return oversized(None)
            if not size:
                return jsonify(error="Файл хоосон байна."), 400
        with lock:
            write_job(job_id, {
                "status": "downloading" if video_id else "processing",
                "expires": time.time() + 3600,
            })
        Thread(target=separate, args=(job_id, folder, source, video_id), daemon=True).start()
        started = True
        return jsonify(id=job_id), 202
    finally:
        if not started:
            shutil.rmtree(folder, ignore_errors=True)
            slots.release()


@app.get("/api/jobs/<job_id>")
def status(job_id):
    with lock:
        job = read_job(job_id)
        if job is None:
            return jsonify(error="Боловсруулалтын хугацаа дууссан байна."), 404
        return jsonify(status=job["status"], error=job.get("error"),
                       paid=job.get("paid", False), payment_available=wire.configured(),
                       amount=wire.PRICE, payment_started=bool(job.get("intent")))


def mark_paid(job_id, job):
    job.update(paid=True, expires=time.time() + PAID_RETENTION)
    with lock:
        write_job(job_id, job)


@app.post("/api/jobs/<job_id>/checkout")
@app.post("/api/jobs/<job_id>/verify-payment")
def payment(job_id):
    if not wire.configured():
        return jsonify(error="Онлайн төлбөр хараахан идэвхжээгүй байна."), 503
    checkout = request.path.endswith("/checkout")
    if not payment_lock.acquire(blocking=False):
        return jsonify(error="Төлбөрийн холболт завгүй байна. Дахин оролдоно уу."), 429
    try:
        with lock:
            job = read_job(job_id)
        if not job or job["status"] != "ready":
            return jsonify(error="Захиалга олдсонгүй."), 404
        if job.get("paid"):
            return jsonify(paid=True)
        intent = job.get("intent")
        state = wire.payment_status(intent, job_id) if intent else None
        if state == "succeeded":
            mark_paid(job_id, job)
            return jsonify(paid=True)
        if not checkout:
            return jsonify(paid=False)
        if (not intent or state == "canceled") and job.get("order_deadline", job["expires"]) < time.time() + 7200:
            return jsonify(error="Файлын хадгалах хугацаа дуусах дөхсөн байна. Дахин боловсруулна уу."), 409
        if not intent or state == "canceled":
            attempt = job.get("attempt", 0) + 1
            key = f"{job_id}-{attempt}"
            intent = wire.create_intent(job_id, f"{key}-intent")
            job.update(intent=intent, attempt=attempt, checkout_url=None,
                       order_deadline=job.get("order_deadline", job["expires"]),
                       expires=time.time() + PAID_RETENTION)
            with lock:
                write_job(job_id, job)
        if not job.get("checkout_url"):
            job["checkout_url"] = wire.create_checkout(intent, job_id, f'{job_id}-{job["attempt"]}-checkout')
            with lock:
                write_job(job_id, job)
        return jsonify(paid=False, url=job["checkout_url"])
    except wire.WireError:
        return jsonify(error="Wire төлбөрийн мэдээлэл авахад алдаа гарлаа. Дахин шалгана уу."), 502
    finally:
        payment_lock.release()


@app.post("/api/wire/webhook")
def wire_webhook():
    secret = os.environ.get("WIRE_WEBHOOK_SECRET")
    if not secret:
        return "", 503
    request.max_content_length = 64 * 1024
    try:
        event = wire.verify_webhook(
            request.get_data(), request.headers.get("WirePayment-Signature", ""), secret,
        )
    except ValueError:
        return "", 400
    if event.get("type") != "payment_intent.succeeded":
        return "", 204
    if not wire.configured():
        return "", 503
    data = event.get("data")
    if not isinstance(data, dict) or data.get("object") != "payment_intent":
        return "", 400
    intent_id = data.get("id")
    if not isinstance(intent_id, str) or not re.fullmatch(r"pi_[A-Za-z0-9]+", intent_id):
        return "", 400
    if not payment_lock.acquire(blocking=False):
        return "", 429
    try:
        intent = wire.get_intent(intent_id)
        metadata = intent.get("metadata")
        job_id = metadata.get("order_id") if isinstance(metadata, dict) else None
        if not isinstance(job_id, str):
            return "", 204
        with lock:
            job = read_job(job_id)
        if not job or job.get("intent") != intent_id or job.get("paid"):
            return "", 204
        if wire.verify(intent, intent_id, job_id) == "succeeded":
            mark_paid(job_id, job)
            return "", 204
        return "", 503
    except wire.WireError:
        return "", 502
    finally:
        payment_lock.release()


@app.get("/api/jobs/<job_id>/<variant>/<stem>")
def result(job_id, variant, stem):
    with lock:
        job = read_job(job_id)
        if not job or job["status"] != "ready" or stem not in STEMS or variant not in ("preview", "full"):
            return jsonify(error="Аудио файл олдсонгүй."), 404
        if variant == "full" and not job.get("paid"):
            return jsonify(error="Бүтэн MP3 татахын тулд төлбөрөө баталгаажуулна уу."), 402
        folder = DATA_DIR / job_id
        name = STEMS[stem]
        path = folder / f"preview-{name}.mp3" if variant == "preview" else folder / "htdemucs" / "track" / f"{name}.mp3"
        response = send_file(path, mimetype="audio/mpeg", max_age=0,
                             as_attachment=variant == "full", download_name=f"{stem}.mp3")
        response.cache_control.private = True
        return response
