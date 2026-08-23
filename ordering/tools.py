"""The seven tools the LLM can call, plus their OpenAI function-calling schemas.

Every tool returns plain JSON-able data. Failures come back as structured
errors the agent can read out loud; nothing raises into the agent loop.

Prices come back twice: `price` for arithmetic, `spoken_price` for the TTS to
read verbatim. Same for `name` / `spoken_name`.
"""

import re
import uuid

from rapidfuzz import fuzz, process

from .cart import Cart, CartError
from .speech import spoken_price
from .toast_client import ToastClient

# Transliteration variants for the names ASR and callers mangle most.
# Every spelling on the right normalizes to the key on the left.
VARIANTS = {
    "chettinad": ["chettinad", "chetinad", "chettinaad", "chetinaad", "chettinand", "chettinadu"],
    "parotta": ["parotta", "paratha", "porotta", "parota", "porota", "barotta", "paratta"],
    "gobhi": ["gobhi", "gobi", "gobbi", "ghobi", "gobhee"],
    "kulambu": ["kuzhambu", "kulambu", "kolumbu", "kulumbu", "kolambu", "kuzhambhu", "kozhambu"],
    "dosa": ["dosa", "dosai", "thosai", "dhosa", "thosa", "dosay", "dhosai"],
    "aappam": ["aappam", "appam", "apam", "aapam", "aappa"],
    "kothu": ["kothu", "kotu", "kottu", "kothhu", "kuthu"],
    "biryani": ["biryani", "biriyani", "briyani", "biriani", "birayani", "biryanni"],
    "idli": ["idli", "idly", "iddli", "idlee", "idlii"],
    "paniyaram": ["paniyaram", "panniyaram", "paniaram", "paniyarum"],
    "saappadu": ["saappadu", "sappadu", "saapadu", "sapadu", "saappad"],
    "kuruma": ["kuruma", "korma", "kurma", "khurma"],
    "kizhi": ["kizhi", "kili", "kizi", "khizi"],
    "uthappam": ["uthappam", "uttapam", "oothappam", "utappam"],
    "idiyappam": ["idiyappam", "idiappam", "iddiyappam", "idiyapam"],
}

# variant spelling -> canonical token
_VARIANT_MAP = {v: canon for canon, spellings in VARIANTS.items() for v in spellings}

# Dropped before matching. Callers say "a dosa", "some rice, please".
FILLER = {"a", "an", "the", "some", "of", "please", "and", "with", "i", "want", "like"}

# When the query names a section, prefer items from it. Keys are canonical
# tokens (post-normalization); values are substrings of the menu group name.
GROUP_HINTS = {
    "dosa": "Thosai / Dosai",
    "biryani": "Biryani",
    "soup": "Suup",
    "dessert": "Inippu",
    "juice": "Kulirpaanam",
    "lassi": "Kulirpaanam",
    "coffee": "Suudana",
    "tea": "Suudana",
}

_SCORE_CUTOFF = 62
_MAX_CANDIDATES = 5
_GROUP_BOOST = 10.0
# If the top two candidates are within this many points, the agent must ask
# instead of assuming. Tuned so "chicken" (95 vs 90) is ambiguous but
# "chettinaad chicken" (95 vs 85.5) is not.
_AMBIGUITY_MARGIN = 8.0


def _normalize(text: str) -> str:
    """Lowercase, drop parentheticals/punctuation/filler, fold transliterations."""
    text = text.lower()
    text = re.sub(r"\([^)]*\)", " ", text)  # "(V)", "(2Pcs)", "(1 Pc Side)"
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [_VARIANT_MAP.get(t, t) for t in text.split() if t not in FILLER]
    return " ".join(tokens)


def _spoken_options(group):
    return [
        {
            "name": o["name"],
            "option_guid": o["guid"],
            "price": o["price"],
            "spoken_price": spoken_price(o["price"]),
        }
        for o in group["options"]
    ]


def _required_groups(item):
    return [
        {
            "group_guid": grp["guid"],
            "group_name": grp["name"],
            "min_selections": grp["minSelections"],
            "max_selections": grp["maxSelections"],
            "options": _spoken_options(grp),
        }
        for grp in item["modifierGroups"]
        if grp["isRequired"]
    ]


TOOL_NAMES = [
    "search_menu",
    "add_item",
    "set_modifier",
    "set_customer_name",
    "remove_item",
    "set_quantity",
    "get_cart",
    "submit_order",
]


class Session:
    """One caller's ordering session: their cart, their client, their order key.

    Create one per connection. Do NOT share across callers: the cart is
    mutable per-call state and two callers on one Session would collide.
    """

    def __init__(self, client=None):
        self.client = client or ToastClient()
        self.menu = self.client.get_menu()
        self.cart = Cart(self.menu)
        self.order_key = str(uuid.uuid4())
        # Precompute normalized names for fuzzy search. Several items can share
        # one normalized name ("Aappam (V)" and "Aappam (1 Pc Side)").
        self._choices = {}
        for guid, item in self.cart.items.items():
            self._choices.setdefault(_normalize(item["name"]), []).append(guid)

    def tool_handlers(self) -> dict:
        """The eight tools, bound to this session. Register these on the LLM."""
        return {name: getattr(self, name) for name in TOOL_NAMES}

    # -- the eight tools ----------------------------------------------------

    def search_menu(self, query: str) -> dict:
        """Fuzzy-match spoken text against item names. Never guesses between ties."""
        normalized = _normalize(query or "")
        if not normalized.strip():
            return {"query": query, "candidates": [], "ambiguous": False}

        tokens = set(normalized.split())
        hint = next((section for t, section in GROUP_HINTS.items() if t in tokens), None)

        matches = process.extract(
            normalized,
            list(self._choices.keys()),
            scorer=fuzz.WRatio,
            limit=20,
            score_cutoff=_SCORE_CUTOFF,
        )

        candidates = []
        for name, score, _ in matches:
            for guid in self._choices[name]:
                item = self.cart.items[guid]
                if hint and hint in item["groupName"]:
                    score += _GROUP_BOOST
                candidates.append(
                    {
                        "item_guid": guid,
                        "name": item["name"],
                        "spoken_name": item.get("spoken_name", item["name"]),
                        "price": item["price"],
                        "spoken_price": spoken_price(item["price"]),
                        "menu_group": item["groupName"],
                        "description": item["description"],
                        "required_modifier_groups": _required_groups(item),
                        "score": round(score, 1),
                    }
                )

        candidates.sort(key=lambda c: -c["score"])
        candidates = candidates[:_MAX_CANDIDATES]

        ambiguous = (
            len(candidates) > 1
            and (candidates[0]["score"] - candidates[1]["score"]) < _AMBIGUITY_MARGIN
        )
        return {"query": query, "candidates": candidates, "ambiguous": ambiguous}

    def add_item(self, item_guid: str, qty: int = 1) -> dict:
        try:
            line_id = self.cart.add(item_guid, qty)
        except CartError as e:
            return {"ok": False, "error": "INVALID_ITEM", "message": str(e)}
        item = self.cart.items[item_guid]
        return {
            "ok": True,
            "line_id": line_id,
            "name": item["name"],
            "spoken_name": item.get("spoken_name", item["name"]),
            "qty": qty,
            "required_modifier_groups": _required_groups(item),
        }

    def set_modifier(self, line_id: str, group_guid: str, option_guid: str) -> dict:
        try:
            self.cart.set_modifier(line_id, group_guid, option_guid)
        except CartError as e:
            return {"ok": False, "error": "INVALID_MODIFIER", "message": str(e)}
        return {"ok": True, **self.get_cart()}

    def set_customer_name(self, name: str) -> dict:
        """Record the caller's first name. Calling again overwrites it."""
        try:
            self.cart.set_customer_name(name)
        except CartError as e:
            return {"ok": False, "error": "INVALID_NAME", "message": str(e)}
        return {"ok": True, "customer_name": self.cart.customer_name}

    def remove_item(self, line_id: str) -> dict:
        """Drop a line. "Actually, take the pappad off"."""
        try:
            self.cart.remove(line_id)
        except CartError as e:
            return {"ok": False, "error": "INVALID_LINE", "message": str(e)}
        return {"ok": True, **self.get_cart()}

    def set_quantity(self, line_id: str, qty: int) -> dict:
        """Change how many of one line. To remove, use remove_item."""
        try:
            self.cart.set_qty(line_id, qty)
        except CartError as e:
            return {"ok": False, "error": "INVALID_LINE", "message": str(e)}
        return {"ok": True, **self.get_cart()}

    def get_cart(self) -> dict:
        return self.cart.snapshot()

    def submit_order(self, readback_confirmed: bool = False) -> dict:
        """Submit to Toast. Refuses (structured, never raises) if not ready."""
        cart = self.cart

        if not cart.lines:
            return {
                "ok": False,
                "error": "EMPTY_CART",
                "message": "There's nothing in the order yet.",
            }

        unfilled = cart.unfilled_required()
        if unfilled:
            first = unfilled[0]
            return {
                "ok": False,
                "error": "MISSING_MODIFIERS",
                "message": (
                    f"Before I can send this I need to know the "
                    f"{first['group_name'].lower()} for the {first['item_name']}."
                ),
                "unfilled_required": unfilled,
            }

        if not cart.customer_name:
            return {
                "ok": False,
                "error": "MISSING_CUSTOMER_NAME",
                "message": "I still need a first name to put the order under.",
            }

        if readback_confirmed:
            cart.readback_confirmed = True
        if not cart.readback_confirmed:
            return {
                "ok": False,
                "error": "READBACK_REQUIRED",
                "message": "Let me read the order back to you before I send it.",
                "cart": cart.snapshot(),
            }

        response = self.client.create_order(
            cart=cart.snapshot(),
            customer={"name": cart.customer_name},
            idempotency_key=self.order_key,
        )
        return {"ok": True, **response}


# -- module-level convenience session --------------------------------------
#
# Tests and the dry run use these. bot.py does NOT: it builds its own Session
# per websocket connection so two simultaneous callers never share a cart.

_session = Session()


def reset(client=None):
    """Start a fresh call on the module-level session."""
    global _session
    _session = Session(client=client)
    return _session


def search_menu(query: str) -> dict:
    return _session.search_menu(query)


def add_item(item_guid: str, qty: int = 1) -> dict:
    return _session.add_item(item_guid, qty)


def set_modifier(line_id: str, group_guid: str, option_guid: str) -> dict:
    return _session.set_modifier(line_id, group_guid, option_guid)


def set_customer_name(name: str) -> dict:
    return _session.set_customer_name(name)


def remove_item(line_id: str) -> dict:
    return _session.remove_item(line_id)


def set_quantity(line_id: str, qty: int) -> dict:
    return _session.set_quantity(line_id, qty)


def get_cart() -> dict:
    return _session.get_cart()


def submit_order(readback_confirmed: bool = False) -> dict:
    return _session.submit_order(readback_confirmed)


# -- OpenAI function-calling schemas ---------------------------------------

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_menu",
            "description": (
                "Look up menu items by what the caller said. Returns candidates with "
                "prices and any required choices. If `ambiguous` is true, or more than "
                "one candidate comes back, ask the caller which one instead of guessing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What the caller called the dish."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_item",
            "description": "Add an item to the order. Returns a line_id to refer to it later.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_guid": {"type": "string", "description": "guid from search_menu."},
                    "qty": {"type": "integer", "minimum": 1, "default": 1},
                },
                "required": ["item_guid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_modifier",
            "description": "Record a choice (spice level, protein, filling) on one line.",
            "parameters": {
                "type": "object",
                "properties": {
                    "line_id": {"type": "string"},
                    "group_guid": {"type": "string"},
                    "option_guid": {"type": "string"},
                },
                "required": ["line_id", "group_guid", "option_guid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_customer_name",
            "description": (
                "Record the first name the order goes under. Ask for this near the "
                "end, once the food is settled, not at the start of the call. Call "
                "it again to correct a name you misheard."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "First name only, exactly as the caller said it.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_item",
            "description": (
                "Take one line off the order, e.g. 'actually, drop the pappad'. "
                "Use the line_id from add_item or get_cart."
            ),
            "parameters": {
                "type": "object",
                "properties": {"line_id": {"type": "string"}},
                "required": ["line_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_quantity",
            "description": (
                "Change how many of one line, e.g. 'make that three parottas'. "
                "To take the line off entirely use remove_item, not quantity zero."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "line_id": {"type": "string"},
                    "qty": {"type": "integer", "minimum": 1},
                },
                "required": ["line_id", "qty"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cart",
            "description": (
                "Current order: lines, subtotal, and unfilled_required listing every "
                "choice still to ask about. Read spoken_name and spoken_price aloud "
                "verbatim. Call this before reading the order back."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_order",
            "description": (
                "Send the order to the kitchen. Only set readback_confirmed after you "
                "have read the full order back and the caller has said yes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "readback_confirmed": {
                        "type": "boolean",
                        "default": False,
                        "description": "True only once the caller has confirmed the readback.",
                    }
                },
            },
        },
    },
]
