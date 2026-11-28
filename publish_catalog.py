"""Publish an approved separated track by job ID."""
import argparse
import array
import json
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from app import DATA_DIR, STEM_CACHE_FILES, read_job


def refresh_preview(track):
    stems = track / "htdemucs" / "track"
    rate = 4000
    pcm = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(stems / "no_vocals.mp3"),
         "-t", "600", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
        check=True, capture_output=True, timeout=60,
    ).stdout
    samples = array.array("h", pcm)
    peaks = [max((abs(value) for value in samples[i:i+rate:8]), default=0)
             for i in range(0, len(samples), rate)]
    duration = len(samples) / rate
    threshold = max(200, max(peaks, default=0) * .10)
    clip_seconds = min(30, duration)
    if clip_seconds <= 0:
        raise ValueError("Empty instrumental")
    last = int(duration - clip_seconds)
    candidates = []
    for start in range(1, last + 1):
        window = peaks[start:start+30]
        active = sum(peak >= threshold for peak in window)
        score = (active + sum(peak >= threshold for peak in window[:5]), sum(window))
        candidates.append((start, active, score))
    suitable = [start for start, active, _ in candidates
                if active >= 24 and peaks[start] >= threshold]
    if suitable:
        start = secrets.choice(suitable)
    elif candidates:
        start = max(candidates, key=lambda candidate: candidate[2])[0]
    else:
        start = 0
    with tempfile.TemporaryDirectory(dir=track) as staging:
        for stem in ("no_vocals", "vocals"):
            output = Path(staging) / f"preview-{stem}.mp3"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(start),
                 "-i", str(stems / f"{stem}.mp3"), "-t", f"{clip_seconds:.3f}",
                 "-c:a", "libmp3lame", "-b:a", "128k", str(output)],
                check=True, timeout=60,
            )
        for output in Path(staging).iterdir():
            output.replace(track / output.name)
    return start


def publish(job_id, title, artist, track_id=None):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("Invalid job ID")
    job = read_job(job_id)
    if not job or job["status"] != "ready" or job.get("operation") == "convert":
        raise ValueError("A ready, unexpired two-stem job is required")
    root = DATA_DIR / "catalog"
    root.mkdir(mode=0o700, exist_ok=True)
    track_id = track_id or uuid4().hex
    with tempfile.TemporaryDirectory(dir=root) as staging:
        track = Path(staging) / track_id
        for name in STEM_CACHE_FILES:
            path = track / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(DATA_DIR / job_id / name, path)
        try:
            refresh_preview(track)
        except subprocess.SubprocessError as error:
            raise ValueError("Could not generate catalogue preview") from error
        (track / "track.json").write_text(json.dumps(
            {"title": title, "artist": artist}, ensure_ascii=False), encoding="utf-8")
        track.rename(root / track_id)
    return track_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("title")
    parser.add_argument("artist")
    parser.add_argument("--approved", action="store_true", required=True)
    args = parser.parse_args()
    title, artist = args.title.strip(), args.artist.strip()
    if not title or not artist:
        parser.error("Title and artist are required")
    print(publish(args.job_id, title, artist))
