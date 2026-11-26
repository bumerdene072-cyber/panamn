"""Private traffic counts and Telegram delivery for PANAMN."""
import json
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

LOCAL_TIME = ZoneInfo("Asia/Ulaanbaatar")


def database(data_dir):
    db = sqlite3.connect(data_dir / "telegram-stats.sqlite3")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS visitors (
            day TEXT NOT NULL, browser TEXT NOT NULL, PRIMARY KEY (day, browser)
        );
        CREATE TABLE IF NOT EXISTS sent (key TEXT PRIMARY KEY);
    """)
    return db


def count_visit(data_dir, browser):
    day = datetime.now(LOCAL_TIME).date().isoformat()
    with closing(database(data_dir)) as db, db:
        db.execute("INSERT OR IGNORE INTO visitors VALUES (?, ?)", (day, browser))


def send_message(token, chat, text):
    payload = json.dumps({"chat_id": chat, "text": text}).encode()
    request = Request(f"https://api.telegram.org/bot{token}/sendMessage",
                      data=payload, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=10) as response:
        if not json.load(response).get("ok"):
            raise ValueError("Telegram rejected the message")


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token) or not re.fullmatch(r"-?[0-9]+", chat):
        return
    data_dir = Path(os.getenv("DATA_DIR", "/tmp/duu-data"))
    today = datetime.now(LOCAL_TIME).date()
    yesterday = today - timedelta(days=1)
    with closing(database(data_dir)) as db:
        def deliver(key, message):
            if db.execute("SELECT 1 FROM sent WHERE key=?", (key,)).fetchone():
                return
            send_message(token, chat, message)
            with db:
                db.execute("INSERT INTO sent VALUES (?)", (key,))

        purchases = 0
        for path in data_dir.glob("*/job.json"):
            try:
                job = json.loads(path.read_text())
                paid_at = job.get("paid_at")
                if not job.get("paid") or not isinstance(paid_at, (int, float)):
                    continue
                local = datetime.fromtimestamp(paid_at, LOCAL_TIME)
                purchases += local.date() == yesterday
                number = job.get("order_number")
                label = f"Захиалга #{number}" if isinstance(number, int) else "Захиалга"
                deliver("paid:" + path.parent.name,
                        f"✅ PANAMN.SITE · {label}\n5,000₮ төлбөр баталгаажлаа.\n{local:%Y-%m-%d %H:%M}")
            except (OSError, ValueError) as error:
                logging.warning("Purchase notification unavailable (%s)", type(error).__name__)
        count, = db.execute("SELECT COUNT(*) FROM visitors WHERE day=?", (yesterday.isoformat(),)).fetchone()
        deliver(f"daily:{yesterday}",
                f"📊 PANAMN.SITE · {yesterday}\nЗочилсон хөтөч: {count}\nБаталгаажсан худалдан авалт: {purchases}")
        with db:
            db.execute("DELETE FROM visitors WHERE day < ?", ((today - timedelta(days=7)).isoformat(),))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, sqlite3.Error) as error:
        logging.error("Telegram notification failed (%s)", type(error).__name__)
        raise SystemExit(1)
