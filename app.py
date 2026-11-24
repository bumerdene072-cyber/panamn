import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import BoundedSemaphore, Lock, Thread, Timer
from uuid import uuid4

from flask import Flask, jsonify, request, send_file

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 51 * 1024 * 1024
LIMIT = 50 * 1024 * 1024
RETENTION = 3600
FORMATS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".mp4"}
STEMS = {"vocals": "vocals", "instrumental": "no_vocals"}
jobs = {}
lock = Lock()
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
    if request.path.startswith("/api/"):
        response.cache_control.no_store = True
    return response


@app.errorhandler(413)
def oversized(_):
    return jsonify(error="Файлын хэмжээ 50 МБ-аас хэтэрсэн байна."), 413


def expire(job_id, folder):
    with lock:
        jobs.pop(job_id, None)
    shutil.rmtree(folder, ignore_errors=True)


def run(command, timeout):
    return subprocess.run(
        command, check=True, timeout=timeout, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )


def separate(job_id, folder, source):
    message = "Файлын дууг уншиж чадсангүй. Өөр файл оруулж үзээрэй."
    try:
        track = folder / "track.wav"
        run(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file", "-format_whitelist",
             "mp3,wav,flac,ogg,mov,mp4,m4a,3gp,3g2,mj2", "-i", str(source), "-map",
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
            if (folder / "htdemucs" / "track" / f"{name}.mp3").stat().st_size == 0:
                raise ValueError("Empty output stem")
        track.unlink()
        with lock:
            jobs[job_id]["status"] = "ready"
    except Exception:
        logging.exception("Separation failed for job %s", job_id)
        shutil.rmtree(folder, ignore_errors=True)
        with lock:
            jobs[job_id].update(status="failed", error=message)
    finally:
        slots.release()
        timer = Timer(RETENTION, expire, args=(job_id, folder))
        timer.daemon = True
        timer.start()


@app.post("/api/jobs")
def submit():
    upload = request.files.get("audio")
    ext = Path(upload.filename or "").suffix.lower() if upload else ""
    if ext not in FORMATS or request.form.get("rights") != "yes":
        return jsonify(error="Зөвшөөрөлтэй, дэмжигдсэн аудио эсвэл видео файл оруулна уу."), 400
    if not slots.acquire(blocking=False):
        return jsonify(error="Сервер дуу боловсруулж байна. Түр хүлээгээд дахин оролдоно уу."), 429
    folder = None
    job_id = uuid4().hex
    try:
        folder = Path(tempfile.mkdtemp(prefix="duu-"))
        source = folder / f"source{ext}"
        upload.save(source)
        if source.stat().st_size > LIMIT:
            return oversized(None)
        if source.stat().st_size == 0:
            return jsonify(error="Файл хоосон байна."), 400
        with lock:
            jobs[job_id] = {"status": "processing", "folder": folder}
        Thread(target=separate, args=(job_id, folder, source), daemon=True).start()
        folder = None
        return jsonify(id=job_id), 202
    finally:
        if folder is not None or job_id not in jobs:
            with lock:
                jobs.pop(job_id, None)
            if folder is not None:
                shutil.rmtree(folder, ignore_errors=True)
            slots.release()


@app.get("/api/jobs/<job_id>")
def status(job_id):
    with lock:
        job = jobs.get(job_id)
        if job is None:
            return jsonify(error="Боловсруулалтын хугацаа дууссан байна."), 404
        return jsonify({key: job[key] for key in ("status", "error") if key in job})


@app.get("/api/jobs/<job_id>/<stem>")
def result(job_id, stem):
    with lock:
        job = jobs.get(job_id)
        if not job or job["status"] != "ready" or stem not in STEMS:
            return jsonify(error="Аудио файл олдсонгүй."), 404
        path = job["folder"] / "htdemucs" / "track" / f"{STEMS[stem]}.mp3"
        response = send_file(path, mimetype="audio/mpeg", max_age=0,
                             as_attachment=request.args.get("download") == "1",
                             download_name=f"{stem}.mp3")
        response.cache_control.private = True
        return response
