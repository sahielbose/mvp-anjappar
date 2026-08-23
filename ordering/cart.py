"""Server-side authoritative cart. The LLM never holds cart state."""

from .speech import number_words, spoken_price

# Long enough for any real first name, short enough to bound a bad transcript.
MAX_NAME_LEN = 40


class CartError(Exception):
    pass


def index_menu(menu: dict) -> dict:
    """Flatten menu into {item_guid: item} with group name attached."""
    index = {}
    for group in menu["menuGroups"]:
        for item in group["items"]:
            index[item["guid"]] = {**item, "groupName": group["name"]}
    return index


class Cart:
    def __init__(self, menu: dict):
        self.menu = menu
        self.items = index_menu(menu)
        self.lines = {}  # line_id -> {"item_guid", "qty", "selections"}
        self._seq = 0
        self.readback_confirmed = False
        self.customer_name = None

    # -- mutation -----------------------------------------------------------

    def _touch(self):
        """Any change to the cart invalidates a prior readback confirmation."""
        self.readback_confirmed = False

    def add(self, item_guid: str, qty: int = 1) -> str:
        if item_guid not in self.items:
            raise CartError(f"unknown item_guid {item_guid}")
        if qty < 1:
            raise CartError("qty must be >= 1")
        self._seq += 1
        line_id = f"L{self._seq}"
        self.lines[line_id] = {"item_guid": item_guid, "qty": qty, "selections": {}}
        self._touch()
        return line_id

    def set_customer_name(self, name: str):
        """Store the caller's first name exactly as given, just trimmed and capped.

        Deliberately no spelling correction or capitalisation: an unusual name
        the agent "fixes" is worse than one it repeats back verbatim.
        """
        name = (name or "").strip()
        if not name:
            raise CartError("name cannot be empty")
        self.customer_name = name[:MAX_NAME_LEN]
        self._touch()

    def remove(self, line_id: str):
        if line_id not in self.lines:
            raise CartError(f"unknown line_id {line_id}")
        del self.lines[line_id]
        self._touch()

    def set_qty(self, line_id: str, qty: int):
        if line_id not in self.lines:
            raise CartError(f"unknown line_id {line_id}")
        if qty < 1:
            raise CartError("qty must be >= 1")
        self.lines[line_id]["qty"] = qty
        self._touch()

    def set_modifier(self, line_id: str, group_guid: str, option_guid: str):
        if line_id not in self.lines:
            raise CartError(f"unknown line_id {line_id}")
        line = self.lines[line_id]
        item = self.items[line["item_guid"]]

        group = next((g for g in item["modifierGroups"] if g["guid"] == group_guid), None)
        if group is None:
            raise CartError(f"group {group_guid} not on item {item['name']}")
        if not any(o["guid"] == option_guid for o in group["options"]):
            raise CartError(f"option {option_guid} not in group {group['name']}")

        chosen = line["selections"].setdefault(group_guid, [])
        if option_guid in chosen:
            return
        if len(chosen) >= group["maxSelections"]:
            # Single-select groups replace; multi-select groups fill up then stop.
            if group["maxSelections"] == 1:
                chosen.clear()
            else:
                raise CartError(f"{group['name']} allows at most {group['maxSelections']}")
        chosen.append(option_guid)
        self._touch()

    # -- reads --------------------------------------------------------------

    def line_price(self, line_id: str) -> float:
        line = self.lines[line_id]
        item = self.items[line["item_guid"]]
        unit = item["price"]
        for group in item["modifierGroups"]:
            for option in group["options"]:
                if option["guid"] in line["selections"].get(group["guid"], []):
                    unit += option["price"]
        return round(unit * line["qty"], 2)

    def subtotal(self) -> float:
        return round(sum(self.line_price(lid) for lid in self.lines), 2)

    def unfilled_required(self) -> list:
        """Required groups that still need a choice, in a shape the agent can speak."""
        missing = []
        for line_id, line in self.lines.items():
            item = self.items[line["item_guid"]]
            for group in item["modifierGroups"]:
                if not group["isRequired"]:
                    continue
                chosen = line["selections"].get(group["guid"], [])
                if len(chosen) < group["minSelections"]:
                    missing.append(
                        {
                            "line_id": line_id,
                            "item_name": item["name"],
                            "group_guid": group["guid"],
                            "group_name": group["name"],
                            "options": [
                                {"name": o["name"], "option_guid": o["guid"], "price": o["price"]}
                                for o in group["options"]
                            ],
                        }
                    )
        return missing

    def snapshot(self) -> dict:
        lines = []
        for line_id, line in self.lines.items():
            item = self.items[line["item_guid"]]
            mods = []
            for group in item["modifierGroups"]:
                for option in group["options"]:
                    if option["guid"] in line["selections"].get(group["guid"], []):
                        mods.append(
                            {
                                "group_name": group["name"],
                                "group_guid": group["guid"],
                                "option_name": option["name"],
                                "option_guid": option["guid"],
                                "price": option["price"],
                                "spoken_price": spoken_price(option["price"]),
                            }
                        )
            price = self.line_price(line_id)
            lines.append(
                {
                    "line_id": line_id,
                    "item_guid": line["item_guid"],
                    "name": item["name"],
                    "spoken_name": item.get("spoken_name", item["name"]),
                    "qty": line["qty"],
                    "spoken_qty": number_words(line["qty"]),
                    "modifiers": mods,
                    "price": price,
                    "spoken_price": spoken_price(price),
                }
            )
        subtotal = self.subtotal()
        return {
            "customer_name": self.customer_name,
            "lines": lines,
            "subtotal": subtotal,
            "spoken_subtotal": spoken_price(subtotal),
            "unfilled_required": self.unfilled_required(),
            "readback_confirmed": self.readback_confirmed,
        }
