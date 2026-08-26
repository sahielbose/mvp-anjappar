"""Text the caller their order summary once submit_order succeeds.

Sending happens fire-and-forget on a daemon thread so the voice pipeline never
blocks on the HTTP round-trip. A failed text is logged and swallowed: the order
is already in and the caller still hears the pickup code out loud, so a texting
hiccup must never take down the call.

The Twilio media stream hands the bot a CallSid but not the phone numbers, so
lookup_call_parties fetches the call record to learn who to text and from which
number.
"""

import os
import threading

import httpx
from loguru import logger

TWILIO_API = "https://api.twilio.com/2010-04-01"

# Twilio rejects a body over 1600 characters outright, so a very long order
# has to be trimmed or the caller gets no text at all. Leave headroom.
MAX_BODY = 1500


def lookup_call_parties(call_sid: str) -> tuple[str | None, str | None]:
    """(caller_number, restaurant_number) for an inbound call, via Twilio REST.

    `from` is the caller (who gets the text); `to` is our Twilio number (who the
    text is sent from). Returns (None, None) on any failure so the caller path
    degrades to no-SMS instead of erroring.
    """
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not (sid and token and call_sid):
        return None, None
    try:
        r = httpx.get(
            f"{TWILIO_API}/Accounts/{sid}/Calls/{call_sid}.json",
            auth=(sid, token),
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("from"), data.get("to")
    except Exception as e:
        logger.warning(f"Could not look up call parties for {call_sid}: {e}")
        return None, None


def build_order_message(cart: dict, pickup_code: str) -> str:
    """Plain-text order summary for SMS.

    Unlike the spoken path, a text can show digits, dollar signs and item names
    verbatim, so this uses the raw fields, not the spoken_* ones.
    """
    name = cart.get("customer_name")
    header = "Anjappar Dublin: order confirmed"
    if name:
        header += f" for {name}"
    head = [header, f"Order #{pickup_code}", ""]

    lines = cart.get("lines", [])
    items = []
    for ln in lines:
        qty = ln.get("qty", 1)
        prefix = f"{qty}x " if qty != 1 else ""
        block = [f"{prefix}{ln['name']} - ${ln['price']:.2f}"]
        block += [f"   - {m['option_name']}" for m in ln.get("modifiers", [])]
        items.append("\n".join(block))

    tail = [
        "",
        f"Total: ${cart.get('subtotal', 0):.2f}",
        "Pickup in ~20 min. Give your order # at the counter.",
    ]

    body = "\n".join(head + items + tail)
    # Drop items off the end until it fits. The order number and the total are
    # the parts the caller actually needs, so they are never what gets cut.
    while items and len(body) > MAX_BODY:
        items.pop()
        note = [f"...and {len(lines) - len(items)} more items"]
        body = "\n".join(head + items + note + tail)
    return body


def _post_sms(to_number: str, from_number: str, body: str) -> None:
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    try:
        r = httpx.post(
            f"{TWILIO_API}/Accounts/{sid}/Messages.json",
            auth=(sid, token),
            data={"To": to_number, "From": from_number, "Body": body},
            timeout=15,
        )
        r.raise_for_status()
        # Twilio returns 201 when it accepts the message, not when the handset
        # gets it. Carrier-side rejections (30034, an unregistered A2P sender)
        # land minutes later on the message record, so this cannot claim
        # delivery, only that Twilio took it.
        body_json = r.json()
        logger.info(
            f"Order SMS accepted by Twilio for {to_number} "
            f"(sid {body_json.get('sid')}, status {body_json.get('status')}); "
            "delivery is not confirmed here"
        )
    except Exception as e:
        logger.warning(f"Order SMS to {to_number} failed: {e}")


def send_order_sms(
    to_number: str | None, from_number: str | None, cart: dict, pickup_code: str
) -> bool:
    """Fire-and-forget the confirmation text. Returns whether a send was started.

    No-op (logged) when either number is missing, e.g. local webrtc mode where
    there is no phone call behind the session.
    """
    if not (to_number and from_number):
        logger.warning("Skipping order SMS: missing caller or restaurant number")
        return False
    body = build_order_message(cart, pickup_code)
    threading.Thread(
        target=_post_sms, args=(to_number, from_number, body), daemon=True
    ).start()
    return True
