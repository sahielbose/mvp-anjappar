"""Tests for the order confirmation text.

Nothing here touches the network: httpx is patched everywhere a send or lookup
could reach out, and the background thread is swapped for an inline call so
assertions are deterministic rather than racing a real thread.
"""

import httpx
import pytest

from . import sms, tools
from .toast_client import ToastClient

CALLER = "+19255550147"
RESTAURANT = "+19253967124"


class _InlineThreading:
    """Stand-in for the threading module that runs the target immediately.

    send_order_sms sends on a background thread so the voice pipeline never
    blocks on Twilio. Tests need the send to have already happened when they
    assert, so they swap the thread for a direct call.
    """

    class Thread:
        def __init__(self, target, args=(), daemon=False):
            self._target, self._args = target, args

        def start(self):
            self._target(*self._args)


@pytest.fixture
def sent(monkeypatch):
    """Capture what would have been POSTed to Twilio, sending nothing."""
    posts = []
    monkeypatch.setattr(sms, "threading", _InlineThreading)
    monkeypatch.setattr(sms, "_post_sms", lambda to, frm, body: posts.append((to, frm, body)))
    return posts


@pytest.fixture
def no_network(monkeypatch):
    """Fail loudly if any test tries to make a real HTTP call."""

    def explode(*a, **k):
        raise AssertionError("test attempted a real HTTP call")

    monkeypatch.setattr(httpx, "post", explode)
    monkeypatch.setattr(httpx, "get", explode)


@pytest.fixture
def call_session(tmp_path):
    """A session that looks like a real phone call, with both numbers known."""
    return tools.Session(
        client=ToastClient(orders_dir=tmp_path / "orders"),
        caller_number=CALLER,
        restaurant_number=RESTAURANT,
    )


def guid_for(session, name):
    for guid, item in session.cart.items.items():
        if item["name"] == name:
            return guid
    raise AssertionError(f"no menu item named {name!r}")


def place_order(session, item="Masala Dosa (V)", qty=1, name="Priya"):
    """Drive a session all the way to a submitted order."""
    added = session.add_item(item_guid=guid_for(session, item), qty=qty)
    for group in added.get("required_modifier_groups", []):
        session.set_modifier(
            line_id=added["line_id"],
            group_guid=group["group_guid"],
            option_guid=group["options"][0]["option_guid"],
        )
    session.set_customer_name(name=name)
    return session.submit_order(readback_confirmed=True)


# -- message format ---------------------------------------------------------


def test_message_matches_the_receipt_format():
    cart = {
        "customer_name": "Priya",
        "lines": [
            {
                "name": "Chicken Chettinad",
                "qty": 2,
                "price": 31.90,
                "modifiers": [{"option_name": "Spicy"}],
            },
            {"name": "Parotta", "qty": 3, "price": 11.85, "modifiers": []},
        ],
        "subtotal": 43.75,
    }
    assert sms.build_order_message(cart, "4729") == (
        "Anjappar Dublin: order confirmed for Priya\n"
        "Order #4729\n"
        "\n"
        "2x Chicken Chettinad - $31.90\n"
        "   - Spicy\n"
        "3x Parotta - $11.85\n"
        "\n"
        "Total: $43.75\n"
        "Pickup in ~20 min. Give your order # at the counter."
    )


def test_single_quantity_has_no_multiplier_prefix():
    cart = {"lines": [{"name": "Pappad", "qty": 1, "price": 3.0, "modifiers": []}], "subtotal": 3.0}
    body = sms.build_order_message(cart, "2345")
    assert "\nPappad - $3.00" in body
    assert "1x" not in body


def test_price_shown_is_the_line_total_not_the_unit_price():
    """price on a cart line is already qty x unit. Printing a unit price next to
    a quantity would read as a much cheaper order than the total below it."""
    cart = {"lines": [{"name": "Idli", "qty": 4, "price": 23.80, "modifiers": []}], "subtotal": 23.80}
    body = sms.build_order_message(cart, "2345")
    assert "4x Idli - $23.80" in body


def test_message_without_a_name_still_reads_correctly():
    cart = {"lines": [], "subtotal": 0.0}
    body = sms.build_order_message(cart, "3456")
    assert body.startswith("Anjappar Dublin: order confirmed\nOrder #3456")
    assert " for None" not in body


def test_every_modifier_appears_under_its_own_item():
    cart = {
        "lines": [
            {
                "name": "Chicken Biryani",
                "qty": 1,
                "price": 18.0,
                "modifiers": [{"option_name": "Medium"}, {"option_name": "Extra Raita"}],
            }
        ],
        "subtotal": 18.0,
    }
    body = sms.build_order_message(cart, "5678")
    assert "   - Medium\n   - Extra Raita" in body


def test_long_order_is_trimmed_but_keeps_the_number_and_total():
    cart = {
        "customer_name": "Priya",
        "lines": [
            {"name": f"Very Long Menu Item Name Number {i}", "qty": 2, "price": 19.99,
             "modifiers": [{"option_name": "Spicy"}]}
            for i in range(60)
        ],
        "subtotal": 1199.40,
    }
    body = sms.build_order_message(cart, "4729")
    assert len(body) <= sms.MAX_BODY
    assert "Order #4729" in body
    assert "Total: $1199.40" in body
    assert "more items" in body


def test_a_short_order_is_never_trimmed():
    cart = {"lines": [{"name": "Pappad", "qty": 1, "price": 3.0, "modifiers": []}], "subtotal": 3.0}
    assert "more items" not in sms.build_order_message(cart, "2345")


# -- send decisions ---------------------------------------------------------


def test_send_posts_once_when_both_numbers_are_known(sent):
    assert sms.send_order_sms(CALLER, RESTAURANT, {"lines": [], "subtotal": 0}, "4729") is True
    assert len(sent) == 1
    to, frm, body = sent[0]
    assert (to, frm) == (CALLER, RESTAURANT)
    assert "Order #4729" in body


def test_no_send_without_a_caller_number(sent):
    """Local webrtc mode has no phone call behind it, so there is nobody to text."""
    assert sms.send_order_sms(None, RESTAURANT, {"lines": [], "subtotal": 0}, "4729") is False
    assert sent == []


def test_no_send_without_a_restaurant_number(sent):
    assert sms.send_order_sms(CALLER, None, {"lines": [], "subtotal": 0}, "4729") is False
    assert sent == []


def test_a_twilio_failure_is_swallowed(monkeypatch):
    """The order is already in. A texting error must never surface as an exception."""

    def boom(*a, **k):
        raise httpx.ConnectError("twilio unreachable")

    monkeypatch.setattr(httpx, "post", boom)
    sms._post_sms(CALLER, RESTAURANT, "hello")  # must not raise


def test_a_twilio_rejection_is_swallowed(monkeypatch):
    """A 400 from Twilio, e.g. an unregistered A2P sender, is logged not raised."""

    def rejected(*a, **k):
        request = httpx.Request("POST", "https://api.twilio.com/")
        return httpx.Response(400, json={"code": 21617}, request=request)

    monkeypatch.setattr(httpx, "post", rejected)
    sms._post_sms(CALLER, RESTAURANT, "hello")  # must not raise


# -- call party lookup ------------------------------------------------------


def test_lookup_maps_from_to_the_caller_and_to_to_the_restaurant(monkeypatch):
    """Backwards would text the restaurant its own receipt and never the caller."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")

    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"from": CALLER, "to": RESTAURANT}, request=request)

    monkeypatch.setattr(httpx, "get", fake_get)
    assert sms.lookup_call_parties("CA123") == (CALLER, RESTAURANT)


def test_lookup_returns_nothing_without_credentials(monkeypatch, no_network):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    assert sms.lookup_call_parties("CA123") == (None, None)


def test_lookup_returns_nothing_without_a_call_sid(monkeypatch, no_network):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    assert sms.lookup_call_parties("") == (None, None)


def test_lookup_failure_degrades_to_no_numbers(monkeypatch):
    """A Twilio outage costs the caller a text, not the call."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")

    def boom(*a, **k):
        raise httpx.ConnectError("twilio unreachable")

    monkeypatch.setattr(httpx, "get", boom)
    assert sms.lookup_call_parties("CA123") == (None, None)


# -- wired into submit_order ------------------------------------------------


def test_submit_texts_the_caller_once_the_order_is_in(call_session, sent):
    result = place_order(call_session)

    assert result["ok"] is True
    assert len(sent) == 1
    to, _, body = sent[0]
    assert to == CALLER
    assert f"Order #{result['pickupCode']}" in body
    assert "Priya" in body


def test_double_submit_texts_the_caller_only_once(call_session, sent):
    """create_order is idempotent, but two texts would still reach the caller."""
    first = place_order(call_session)
    second = call_session.submit_order(readback_confirmed=True)

    assert first["orderGuid"] == second["orderGuid"]
    assert len(sent) == 1


def test_a_blocked_submit_never_texts(call_session, sent):
    """Nothing is in the kitchen yet, so a receipt would be a lie."""
    added = call_session.add_item(item_guid=guid_for(call_session, "Masala Dosa (V)"))
    for group in added.get("required_modifier_groups", []):
        call_session.set_modifier(
            line_id=added["line_id"],
            group_guid=group["group_guid"],
            option_guid=group["options"][0]["option_guid"],
        )

    assert call_session.submit_order()["error"] == "MISSING_CUSTOMER_NAME"
    call_session.set_customer_name(name="Priya")
    assert call_session.submit_order()["error"] == "READBACK_REQUIRED"
    assert sent == []


def test_submit_still_succeeds_when_texting_fails(call_session, monkeypatch):
    monkeypatch.setattr(sms, "threading", _InlineThreading)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("x")))

    result = place_order(call_session)
    assert result["ok"] is True
    assert result["pickupCode"]


def test_local_mode_submits_without_texting(tmp_path, sent):
    """A browser-mic session has no numbers, and must still be able to order."""
    session = tools.Session(client=ToastClient(orders_dir=tmp_path / "orders"))

    assert place_order(session)["ok"] is True
    assert sent == []
