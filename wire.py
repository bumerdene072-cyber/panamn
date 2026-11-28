import hmac
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PRICE = 5000


class WireError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def configured():
    origin = urlsplit(os.environ.get("PUBLIC_ORIGIN", ""))
    return bool(os.environ.get("WIRE_API_KEY") and origin.scheme == "https" and
                origin.hostname and not origin.username and not origin.password and
                origin.path in ("", "/") and not origin.query and not origin.fragment)


def call(path, body=None, key=None):
    headers = {"Authorization": "Bearer " + os.environ["WIRE_API_KEY"], "Accept": "application/json"}
    payload = None
    if body is not None:
        headers.update({"Content-Type": "application/json", "Idempotency-Key": key})
        payload = json.dumps(body).encode()
    try:
        with build_opener(NoRedirect()).open(
            Request("https://api.wire.mn/v1/" + path, data=payload, headers=headers), timeout=10
        ) as response:
            result = json.load(response)
    except HTTPError as error:
        raise WireError("Wire API returned an error", error.code) from error
    except (URLError, OSError, ValueError) as error:
        raise WireError("Wire request failed") from error
    if not isinstance(result, dict):
        raise WireError("Unexpected Wire response")
    return result


def verify(intent, intent_id, job_id):
    if (intent.get("id") != intent_id or intent.get("amount") != PRICE or
            intent.get("currency") != "MNT" or
            (intent.get("metadata") or {}).get("order_id") != job_id):
        raise WireError("Wire order mismatch")
    return intent.get("status")


def create_intent(job_id, key, order_number=None):
    intent = call("payment_intents", {
        "amount": PRICE, "currency": "MNT", "description": f"Захиалга #{order_number}" if order_number is not None else "Дуу Салгагч — бүтэн таталт",
        "metadata": {"order_id": job_id},
        "allowed_operators": ["qpay"],
    }, key)
    intent_id = intent.get("id", "")
    if not isinstance(intent_id, str) or not re.fullmatch(r"pi_[A-Za-z0-9]+", intent_id):
        raise WireError("Invalid payment intent ID")
    verify(intent, intent_id, job_id)
    return intent_id


def get_intent(intent_id):
    if not isinstance(intent_id, str) or not re.fullmatch(r"pi_[A-Za-z0-9]+", intent_id):
        raise WireError("Invalid payment intent ID")
    return call("payment_intents/" + intent_id)


def payment_status(intent_id, job_id):
    return verify(get_intent(intent_id), intent_id, job_id)


def verify_webhook(body, signature, secret):
    match = re.fullmatch(r"t=([0-9]{1,12}),\s*v1=([a-f0-9]{64})", signature)
    if match is None or abs(time.time() - int(match[1])) > 300:
        raise ValueError("Invalid Wire signature timestamp")
    expected = hmac.digest(secret.encode(), match[1].encode() + b"." + body, "sha256")
    if not hmac.compare_digest(expected, bytes.fromhex(match[2])):
        raise ValueError("Invalid Wire signature")
    event = json.loads(body)
    if not isinstance(event, dict) or event.get("object") != "event":
        raise ValueError("Invalid Wire event")
    return event


def checkout_url(session, intent_id):
    session_id = session.get("id")
    if (not isinstance(session_id, str) or not re.fullmatch(r"cs_[A-Za-z0-9_-]+", session_id)
            or session.get("payment_intent") != intent_id
            or session.get("url") != f"https://pay.wire.mn/c/{session_id}"):
        raise WireError("Invalid checkout session")
    return session["url"] if session.get("status") == "open" else None


def open_checkout(url, intent_id):
    match = re.fullmatch(r"https://pay\.wire\.mn/c/(cs_[A-Za-z0-9_-]+)", url)
    if not match:
        return None
    try:
        return checkout_url(call("checkout/sessions/" + match[1]), intent_id)
    except WireError as error:
        if error.status == 404:
            return None
        raise


def intent_expired(intent):
    expiry = intent.get("expires_at")
    return isinstance(expiry, (int, float)) and expiry <= time.time()


def cancel_intent(intent_id, job_id, key):
    result = call("payment_intents/" + intent_id + "/cancel", {}, key)
    return verify(result, intent_id, job_id)


def create_checkout(intent_id, job_id, key):
    origin = os.environ["PUBLIC_ORIGIN"].rstrip("/")
    result = call("checkout/sessions", {
        "payment_intent": intent_id,
        "success_url": f"{origin}/?wire=return&job={job_id}",
        "cancel_url": f"{origin}/?wire=cancel&job={job_id}",
    }, key)
    url = checkout_url(result, intent_id)
    if not url:
        raise WireError("Wire did not open the checkout session")
    return url
