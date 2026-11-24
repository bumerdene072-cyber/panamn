import json
import os
import re
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PRICE = 5000


class WireError(Exception):
    pass


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


def create_intent(job_id, key):
    intent = call("payment_intents", {
        "amount": PRICE, "currency": "MNT", "description": "Дуу Салгагч — бүтэн таталт",
        "metadata": {"order_id": job_id},
    }, key)
    intent_id = intent.get("id", "")
    if not isinstance(intent_id, str) or not re.fullmatch(r"pi_[A-Za-z0-9]+", intent_id):
        raise WireError("Invalid payment intent ID")
    verify(intent, intent_id, job_id)
    return intent_id


def payment_status(intent_id, job_id):
    return verify(call("payment_intents/" + intent_id), intent_id, job_id)


def create_checkout(intent_id, key):
    origin = os.environ["PUBLIC_ORIGIN"].rstrip("/")
    result = call("checkout/sessions", {
        "payment_intent": intent_id,
        "success_url": origin + "/?wire=return",
        "cancel_url": origin + "/?wire=cancel",
    }, key)
    url = result.get("url", "")
    if result.get("payment_intent") != intent_id or not isinstance(url, str) or not re.fullmatch(
        r"https://pay\.wire\.mn/c/[A-Za-z0-9_-]+", url
    ):
        raise WireError("Invalid checkout URL")
    return url
