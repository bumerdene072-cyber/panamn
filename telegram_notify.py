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
        CREATE TABLE IF NOT EXISTS funnel (
            stage TEXT NOT NULL, ref TEXT NOT NULL, day TEXT NOT NULL,
            source TEXT NOT NULL, PRIMARY KEY(stage, ref, day)
        );
        CREATE INDEX IF NOT EXISTS funnel_day ON funnel(day, source, stage);
        CREATE INDEX IF NOT EXISTS funnel_ref ON funnel(ref, stage);
        CREATE TABLE IF NOT EXISTS sent (key TEXT PRIMARY KEY);
    """)
    return db


def count_visit(data_dir, browser):
    day = datetime.now(LOCAL_TIME).date().isoformat()
    with closing(database(data_dir)) as db, db:
        db.execute("INSERT OR IGNORE INTO visitors VALUES (?, ?)", (day, browser))


SOURCES = {"facebook": "Facebook", "tiktok": "TikTok", "group": "Facebook групп",
           "direct": "Шууд / эх үүсвэргүй"}


def track_stage(data_dir, stage, ref, source="direct"):
    day = datetime.now(LOCAL_TIME).date().isoformat()
    try:
        with closing(database(data_dir)) as db, db:
            if stage in ("preview", "checkout", "paid"):
                row = db.execute("SELECT source FROM funnel WHERE stage='started' AND ref=? LIMIT 1",
                                 (ref,)).fetchone()
                if row:
                    source = row[0]
            db.execute("INSERT OR IGNORE INTO funnel VALUES (?,?,?,?)", (stage, ref, day, source))
    except sqlite3.Error:
        logging.warning("Could not record funnel stage %s", stage)


def funnel_summary(db, day, visitors, purchases):
    groups = {}
    for source, stage, count in db.execute(
        "SELECT source,stage,COUNT(*) FROM funnel WHERE day=? GROUP BY source,stage", (day,)
    ):
        groups.setdefault(source, {})[stage] = count
    for source, count in db.execute(
        """SELECT a.source,COUNT(*) FROM funnel a WHERE a.day=? AND a.stage='checkout'
           AND NOT EXISTS (SELECT 1 FROM funnel b WHERE b.ref=a.ref AND b.stage='paid')
           GROUP BY a.source""", (day,)
    ):
        groups.setdefault(source, {})["unpaid"] = count
    totals = {key:sum(g.get(key, 0) for g in groups.values())
              for key in ("started", "preview", "checkout", "unpaid")}
    lines = [f"📊 PANAMN.SITE · {day}", f"Зочилсон хөтөч: {visitors}",
             f"Салгаж эхэлсэн: {totals['started']}", f"Preview сонссон: {totals['preview']}",
             f"QPay нээсэн: {totals['checkout']}", f"Төлсөн: {purchases}",
             f"Тайлан илгээх үед QPay нээгээд төлөөгүй: {totals['unpaid']}"]
    if groups:
        lines += ["", "Суваг · зочлол → салгалт → preview → QPay → төлсөн (баталгаажаагүй)"]
        for source, label in SOURCES.items():
            if source not in groups:
                continue
            g = groups[source]
            values = " → ".join(str(g.get(k, 0)) for k in ("visit","started","preview","checkout","paid"))
            lines.append(f"{label}: {values} ({g.get('unpaid', 0)})")
    return "\n".join(lines)

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
        deliver(f"daily:{yesterday}", funnel_summary(db, yesterday.isoformat(), count, purchases))
        with db:
            db.execute("DELETE FROM funnel WHERE day < ?", ((today - timedelta(days=30)).isoformat(),))
            db.execute("DELETE FROM visitors WHERE day < ?", ((today - timedelta(days=7)).isoformat(),))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, sqlite3.Error) as error:
        logging.error("Telegram notification failed (%s)", type(error).__name__)
        raise SystemExit(1)
