"""Publish an approved separated track by job ID."""
import argparse
import json
import re
import shutil
import tempfile
from uuid import uuid4

from app import DATA_DIR, STEM_CACHE_FILES, read_job


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
        from pathlib import Path
        track = Path(staging) / track_id
        for name in STEM_CACHE_FILES:
            path = track / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(DATA_DIR / job_id / name, path)
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
