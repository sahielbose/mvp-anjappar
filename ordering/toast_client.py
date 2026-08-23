"""Stub Toast client. Same signatures as the real one, so it can be swapped later."""

import hashlib
import json
import uuid
from pathlib import Path

from .speech import spoken_digits

MENU_PATH = Path(__file__).parent / "menu.json"
ORDERS_DIR = Path(__file__).parent / "orders"

# Digits only, and no 0 or 1: over a phone line those are the ones callers
# mishear as O and I. Everything left is unambiguous said out loud.
PICKUP_ALPHABET = "23456789"
PICKUP_CODE_LEN = 4


def pickup_code_for(idempotency_key: str) -> str:
    """Short spoken-friendly code, derived from the idempotency key.

    Ours, not Toast's: the real POS issues its own order numbers, so this is a
    separate field from orderGuid and easy to drop when the real client lands.
    """
    digest = hashlib.sha256(idempotency_key.encode()).digest()
    return "".join(PICKUP_ALPHABET[b % len(PICKUP_ALPHABET)] for b in digest[:PICKUP_CODE_LEN])


class ToastClient:
    def __init__(self, menu_path=MENU_PATH, orders_dir=ORDERS_DIR):
        self.menu_path = Path(menu_path)
        self.orders_dir = Path(orders_dir)

    def get_menu(self) -> dict:
        return json.loads(self.menu_path.read_text())

    def create_order(self, cart: dict, customer: dict, idempotency_key: str) -> dict:
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        path = self.orders_dir / f"{idempotency_key}.json"

        # Replaying the same key returns the stored order without rewriting it.
        if path.exists():
            return json.loads(path.read_text())["response"]

        code = pickup_code_for(idempotency_key)
        response = {
            "orderGuid": str(uuid.uuid4()),
            "status": "SUBMITTED",
            "pickupCode": code,
            "spokenPickupCode": spoken_digits(code),
        }
        path.write_text(
            json.dumps(
                {
                    "idempotencyKey": idempotency_key,
                    "cart": cart,
                    "customer": customer,
                    "response": response,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return response
