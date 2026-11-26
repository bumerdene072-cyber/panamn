import json
import logging
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from threading import BoundedSemaphore, Lock, Thread
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4
from zipfile import ZipFile

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
MEDIA_FORMATS = "mp3,wav,flac,ogg,mov,mp4,m4a,3gp,3g2,mj2,matroska,webm"
STEMS = {"vocals": "vocals", "instrumental": "no_vocals"}
lock = Lock()
payment_lock = Lock()
slots = BoundedSemaphore(1)
search_slots = BoundedSemaphore(2)
WORKER_ID = uuid4().hex
ACTIVE_STATUSES = {"downloading", "processing"}


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
    link = link.strip()
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


def copy_registered_source(video_id, folder):
    library = os.environ.get("PANAMN_SOURCE_DIR")
    if not library:
        return None
    for extension in sorted(FORMATS):
        registered = Path(library) / f"{video_id}{extension}"
        try:
            with registered.open("rb") as source:
                size = os.fstat(source.fileno()).st_size
                if not 0 < size <= LIMIT:
                    raise YouTubeImportError("Бүртгэлтэй эх файл хоосон эсвэл 50 МБ-аас их байна.")
                destination = folder / f"registered{extension}"
                with destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
                return destination
        except FileNotFoundError:
            continue
    return None


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


def update_processing(job_id, **fields):
    with lock:
        job = read_job(job_id)
        if job is not None:
            job.update(fields)
            write_job(job_id, job)


def run_demucs(job_id, folder, track):
    device = os.environ.get("DEMUCS_DEVICE", "cpu")
    command = [
        sys.executable, "-m", "demucs", "-n", "htdemucs", "-d", device,
        "--two-stems", "vocals", "--mp3", "--mp3-bitrate", "320",
        "--jobs", os.environ.get("DEMUCS_JOBS", "0"), "-o", str(folder), str(track),
    ]
    timeout = 1200
    deadline = time.monotonic() + timeout
    progress = 0
    log_path = folder / "demucs.log"
    update_processing(job_id, phase="separating", progress=0)
    with log_path.open("wb") as log, subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=log, stdin=subprocess.DEVNULL,
    ) as process:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                returncode = process.wait(timeout=min(1, remaining))
            except subprocess.TimeoutExpired:
                # Demucs' tqdm progress includes carriage-return-delimited percentages.
                values = re.findall(rb"([0-9]{1,3})%\|", log_path.read_bytes())
                if values:
                    current = min(99, int(values[-1]))
                    if current > progress:
                        progress = current
                        update_processing(job_id, progress=progress)
                continue
            if returncode:
                logging.error("Demucs failed: %s", log_path.read_text(errors="replace")[-1200:])
                raise subprocess.CalledProcessError(returncode, command)
            break
    log_path.unlink()
    update_processing(job_id, phase="encoding", progress=None)


def process_audio(job_id, folder, source, video_id, operation):
    message = "Файлын дууг уншиж чадсангүй. Өөр файл оруулж үзээрэй."
    try:
        if video_id:
            message = (
                "YouTube-ээс аудио авч чадсангүй. Нийтэд нээлттэй, 10 минут хүртэлх "
                "бичлэгийн холбоос эсвэл аудио файл оруулна уу."
            )
            try:
                source = copy_registered_source(video_id, folder) or download_youtube(video_id, folder)
            except YouTubeImportError as error:
                message = str(error)
                raise
            update_processing(job_id, status="processing", phase="preparing")
            message = "YouTube-ээс авсан аудиог уншиж чадсангүй."
        if operation == "convert":
            update_processing(job_id, phase="encoding")
            message = "Аудиог MP3 болгож чадсангүй. Өөр линк оруулж үзээрэй."
            run(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
                 "-format_whitelist", MEDIA_FORMATS, "-i", str(source), "-map", "0:a:0",
                 "-vn", "-t", "600", "-c:a", "libmp3lame", "-b:a", "192k",
                 str(folder / "source.mp3")], 90)
            source.unlink()
            with lock:
                write_job(job_id, {"status": "ready", "operation": "convert",
                                   "expires": time.time() + RETENTION})
            return
        track = folder / "track.wav"
        run(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file", "-format_whitelist",
             MEDIA_FORMATS, "-i", str(source), "-map",
             "0:a:0", "-vn", "-ac", "2", "-ar", "44100", "-t", "601", str(track)], 90)
        source.unlink()
        # Stereo 16-bit PCM at 44.1 kHz, with room for the WAV header.
        if track.stat().st_size > 600 * 44100 * 4 + 1024:
            message = "10 минут хүртэлх бичлэг оруулна уу."
            raise ValueError(message)
        message = "AI боловсруулалт амжилтгүй боллоо. Дахин оролдоно уу."
        run_demucs(job_id, folder, track)
        for name in STEMS.values():
            stem = folder / "htdemucs" / "track" / f"{name}.mp3"
            if not stem.stat().st_size:
                raise ValueError("Empty output stem")
            run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(stem), "-t", "30",
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


@app.get("/api/youtube/search")
def search_youtube():
    query = " ".join(request.args.get("q", "").split())
    if not 2 <= len(query) <= 120:
        return jsonify(error="Дуу эсвэл дуучны нэрээс 2–120 тэмдэгт оруулна уу."), 400
    if not search_slots.acquire(blocking=False):
        return jsonify(error="Хайлт түр завгүй байна. Дахин оролдоно уу."), 429
    try:
        completed = subprocess.run([
            sys.executable, "-m", "yt_dlp", "--ignore-config", "--flat-playlist",
            "--dump-single-json", "--socket-timeout", "8", "--extractor-retries", "0",
            "--", f"ytsearch10:{query}",
        ], check=True, timeout=18, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        results = []
        for item in json.loads(completed.stdout).get("entries", []):
            if not isinstance(item, dict):
                continue
            video_id = item.get("id")
            duration = item.get("duration")
            if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                continue
            if item.get("live_status") in {"is_live", "is_upcoming"} or (
                isinstance(duration, (int, float)) and duration > 600
            ):
                continue
            results.append({
                "id": video_id,
                "title": str(item.get("title") or video_id)[:200],
                "channel": str(item.get("channel") or item.get("uploader") or "")[:120],
                "duration": duration if isinstance(duration, (int, float)) else None,
            })
            if len(results) == 6:
                break
        return jsonify(results=results)
    except (subprocess.SubprocessError, ValueError):
        return jsonify(error="YouTube хайлт түр ажиллахгүй байна. Линкээ шууд оруулж болно."), 502
    finally:
        search_slots.release()


@app.post("/api/jobs")
def submit():
    upload = request.files.get("audio")
    ext = Path(upload.filename or "").suffix.lower() if upload else ""
    link = request.form.get("youtube_url", "").strip()
    video_id = youtube_video_id(link) if link else None
    operation = request.form.get("operation", "separate")
    if operation not in {"convert", "separate"} or (operation == "convert" and not video_id):
        return jsonify(error="YouTube бичлэгийн зөв холбоос оруулна уу."), 400
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
                "phase": "downloading" if video_id else "preparing",
                "started": time.time(),
                "worker": WORKER_ID,
                "operation": operation,
                "expires": time.time() + 3600,
            })
        Thread(target=process_audio, args=(job_id, folder, source, video_id, operation), daemon=True).start()
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
        if job["status"] in ACTIVE_STATUSES and job.get("worker") != WORKER_ID:
            return jsonify(status="failed", error="Сервер дахин ассан тул энэ оролдлого тасарлаа. Дуугаа дахин оруулна уу.")
        return jsonify(status=job["status"], error=job.get("error"),
                       operation=job.get("operation", "separate"),
                       phase=job.get("phase"), progress=job.get("progress"),
                       elapsed=int(max(0, time.time() - job["started"])) if "started" in job else None,
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
        if not job or job["status"] != "ready" or job.get("operation") == "convert":
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


@app.get("/api/jobs/<job_id>/mp3")
def converted_mp3(job_id):
    with lock:
        job = read_job(job_id)
        if not job or job["status"] != "ready" or job.get("operation") != "convert":
            return jsonify(error="MP3 файл олдсонгүй."), 404
        return send_file(DATA_DIR / job_id / "source.mp3", mimetype="audio/mpeg",
                         as_attachment=True, download_name="PANAMN.mp3", max_age=0)


@app.get("/api/jobs/<job_id>/download")
def download_bundle(job_id):
    with lock:
        job = read_job(job_id)
        if not job or job["status"] != "ready" or job.get("operation") == "convert":
            return jsonify(error="Аудио файл олдсонгүй."), 404
        if not job.get("paid"):
            return jsonify(error="Төлбөрөө баталгаажуулна уу."), 402
    track = DATA_DIR / job_id / "htdemucs" / "track"
    archive = tempfile.TemporaryFile()
    try:
        with ZipFile(archive, "w") as bundle:
            for stem, name in STEMS.items():
                bundle.write(track / f"{name}.mp3", f"{stem}.mp3")
        archive.seek(0)
        return send_file(archive, mimetype="application/zip", as_attachment=True,
                         download_name="PANAMN-MP3.zip", max_age=0)
    except FileNotFoundError:
        archive.close()
        return jsonify(error="Аудио файл олдсонгүй."), 404
    except Exception:
        archive.close()
        raise


@app.get("/api/jobs/<job_id>/<variant>/<stem>")
def result(job_id, variant, stem):
    with lock:
        job = read_job(job_id)
        if not job or job["status"] != "ready" or job.get("operation") == "convert" or stem not in STEMS or variant not in ("preview", "full"):
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
