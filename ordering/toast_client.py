"""Stub Toast client. Same signatures as the real one, so it can be swapped later."""

import json
import uuid
from pathlib import Path

MENU_PATH = Path(__file__).parent / "menu.json"
ORDERS_DIR = Path(__file__).parent / "orders"


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

        response = {"orderGuid": str(uuid.uuid4()), "status": "SUBMITTED"}
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
