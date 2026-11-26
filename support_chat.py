import json
import logging
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.request import Request, urlopen

from flask import Blueprint, jsonify, request, send_from_directory

support = Blueprint("support", __name__)


def connect():
    directory = Path(os.getenv("DATA_DIR", "/tmp/duu-data"))
    db = sqlite3.connect(directory / "support.sqlite3")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY, chat TEXT NOT NULL, body TEXT NOT NULL,
            direction TEXT NOT NULL, ts INTEGER NOT NULL,
            telegram_id INTEGER UNIQUE, update_id INTEGER UNIQUE
        );
        CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat, id);
        CREATE TABLE IF NOT EXISTS cursor (id INTEGER PRIMARY KEY, offset INTEGER NOT NULL);
    """)
    return db


@support.get("/help")
def help_page():
    return send_from_directory(Path(__file__).parent / "static", "help.html")


@support.route("/api/support", methods=["GET", "POST"])
def messages():
    chat = request.headers.get("X-Support-Chat", "")
    new_chat = not re.fullmatch(r"[a-f0-9]{48}", chat)
    if new_chat:
        if request.method == "POST":
            return jsonify(error="Чатыг дахин нээгээд оролдоно уу."), 409
        chat = secrets.token_hex(24)
    with closing(connect()) as db, db:
        if request.method == "POST":
            request.max_content_length = 8192
            payload = request.get_json(silent=True)
            body = payload.get("text") if isinstance(payload, dict) else None
            if not isinstance(body, str) or not 1 <= len(body.strip()) <= 1000:
                return jsonify(error="1–1000 тэмдэгттэй мессеж бичнэ үү."), 400
            now = int(time.time())
            db.execute("BEGIN IMMEDIATE")
            recent, total = db.execute(
                """SELECT COUNT(CASE WHEN chat=? THEN 1 END), COUNT(*)
                   FROM messages WHERE direction='out' AND ts>?""", (chat, now - 60)
            ).fetchone()
            if recent >= 5 or total >= 20:
                return jsonify(error="Түр хүлээгээд дахин оролдоно уу."), 429
            db.execute("INSERT INTO messages(chat,body,direction,ts) VALUES(?,?,'out',?)",
                       (chat, body.strip(), now))
        rows = db.execute(
            """SELECT body,direction FROM (
                SELECT id,body,direction FROM messages WHERE chat=? ORDER BY id DESC LIMIT 100
            ) ORDER BY id""", (chat,)
        ).fetchall()
    response = jsonify(messages=[dict(row) for row in rows])
    response.cache_control.no_store = True
    if new_chat:
        response = jsonify(chat=chat, messages=[])
        response.cache_control.no_store = True
    return response


def relay():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    owner = int(os.environ["TELEGRAM_CHAT_ID"])
    if owner <= 0 or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValueError("Expected a private Telegram owner chat")

    def api(method, payload):
        req = Request(f"https://api.telegram.org/bot{token}/{method}",
                      data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as response:
            result = json.load(response)
        if not result.get("ok"):
            raise ValueError("Telegram API rejected request")
        return result["result"]

    with closing(connect()) as db:
        offset = db.execute("SELECT offset FROM cursor WHERE id=1").fetchone()
        updates = api("getUpdates", {"offset": offset[0] if offset else 0,
                                    "allowed_updates": ["message"], "limit": 100})
        for update in updates:
            msg = update.get("message", {})
            with db:
                if (msg.get("chat", {}).get("id") == owner
                        and msg.get("from", {}).get("id") == owner
                        and isinstance(msg.get("text"), str)):
                    original = msg.get("reply_to_message", {}).get("message_id")
                    source = db.execute("SELECT chat FROM messages WHERE telegram_id=?", (original,)).fetchone()
                    if source:
                        db.execute(
                            """INSERT OR IGNORE INTO messages(chat,body,direction,ts,update_id)
                               VALUES(?,?,'in',?,?)""",
                            (source[0], msg["text"][:4000], int(time.time()), update["update_id"])
                        )
                db.execute("INSERT OR REPLACE INTO cursor VALUES(1,?)", (update["update_id"] + 1,))
        for row in db.execute(
            "SELECT id,body FROM messages WHERE direction='out' AND telegram_id IS NULL ORDER BY id LIMIT 8"
        ).fetchall():
            sent = api("sendMessage", {"chat_id": owner,
                       "text": "💬 PANAMN.SITE · Тусламж\nЭнэ мессеж дээр Reply дарж хариулаарай.\n\n" + row["body"]})
            with db:
                db.execute("UPDATE messages SET telegram_id=? WHERE id=?", (sent["message_id"], row["id"]))
        with db:
            db.execute("DELETE FROM messages WHERE ts<?", (int(time.time()) - 30 * 86400,))


if __name__ == "__main__":
    try:
        relay()
    except Exception as error:
        logging.error("Support relay failed (%s, HTTP %s)", type(error).__name__, getattr(error, "code", "-"))
        raise SystemExit(1)
