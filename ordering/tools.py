"""The seven tools the LLM can call, plus their OpenAI function-calling schemas.

Every tool returns plain JSON-able data. Failures come back as structured
errors the agent can read out loud; nothing raises into the agent loop.

Prices come back twice: `price` for arithmetic, `spoken_price` for the TTS to
read verbatim. Same for `name` / `spoken_name`.
"""

import re
import uuid

from rapidfuzz import fuzz, process

from . import availability
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

# Semantic aliases: a word the caller is likely to use -> the word that actually
# appears on this menu. VARIANTS folds misspellings of one word onto a canonical
# form; these are genuinely different words, and crucially BOTH sides appear on
# this menu -- "Seeraga Samba Goat Biryani" and "Mutton Masala" are both real
# items. So these are applied as *extra query variants*, never by rewriting the
# query in place: folding goat->mutton would make the goat biryani unfindable.
#
# Every entry is grounded in a word that appears in an actual item name. Words
# for things this kitchen doesn't serve (naan, samosa, vindaloo) are deliberately
# absent so the agent says "I don't see it" rather than steering to a near-miss.
ALIASES = {
    # Indian English: mutton means goat. Both words are on the menu.
    "goat": ["mutton", "aatu"],
    "lamb": ["mutton", "aatu"],
    "mutton": ["goat", "kari", "aatu"],
    "chicken": ["kozhi"],
    "kozhi": ["chicken"],
    "fish": ["meen"],
    "meen": ["fish"],
    "prawn": ["shrimp"],
    "shrimp": ["prawn"],
    "egg": ["muttai"],
    "muttai": ["egg"],
    # Vegetables under their English names.
    "eggplant": ["kathirikai"],
    "aubergine": ["kathirikai"],
    "brinjal": ["kathirikai"],
    "cauliflower": ["gobhi"],
    "vegetable": ["kaikari", "veg"],
    "vegetarian": ["veg"],
    "kaikari": ["vegetable"],
    "garlic": ["poondu"],
    "cottage cheese": ["paneer"],
    "anchovy": ["nethili"],
    "anchovies": ["nethili"],
    "king fish": ["vanjaram"],
    "kingfish": ["vanjaram"],
    "tender coconut": ["elaneer"],
    # Drinks. "kaapi" and "tea" both appear; "coke" never does, only "Coca-Cola".
    "coffee": ["kaapi"],
    "kaapi": ["coffee"],
    "filter coffee": ["kaapi"],
    "chai": ["tea"],
    "tea": ["chai"],
    "coke": ["coca cola"],
    "cola": ["coca cola"],
    "yogurt": ["curd"],
    "yoghurt": ["curd"],
    "curd": ["yogurt"],
    "buttermilk": ["mor"],
    "mor": ["buttermilk"],
}

# One substitution per variant. Callers mix languages inside a phrase ("egg
# dosa", "goat curry") but effectively never need two swaps at once, and the
# cap keeps a long query from fanning out.
_MAX_QUERY_VARIANTS = 12

# Every word that appears on the left of ALIASES, tokenized. A query word found
# here is "explained" even though it never appears in an item name.
_ALIAS_TOKENS = {tok for phrase in ALIASES for tok in phrase.split()}

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
# A query word that is on neither the menu nor the alias list is evidence the
# caller wants something this kitchen doesn't make. Fuzzy matching alone can't
# tell "lamb curry" from "beef curry" -- both score 85.5 against Pepper Chicken
# Curry, because "curry" carries the match. Below this score an unexplained word
# suppresses the result entirely, so the agent says "I don't see beef" instead of
# offering chicken. Above it, misspellings like "chapati" (93) still get through.
_CONFIDENT_SCORE = 90.0

# Meat words that appear in item names, and the words a caller uses to signal a
# vegetarian request. Fuzzy matching happily answers "paneer tikka" with Chicken
# Tikka Masala because "tikka" carries the score. At a South Indian restaurant
# veg/non-veg is a hard line for a lot of guests, so a veg request never gets a
# meat dish offered ahead of a vegetarian one.
MEAT_TOKENS = {
    "chicken", "kozhi", "nattukozhi", "mutton", "goat", "aatu", "kari", "lamb",
    "fish", "meen", "nethili", "vanjaram", "pomfret", "prawn", "shrimp", "crab",
    "seafood", "egg", "muttai", "omelette", "liver",
}
VEG_REQUEST_TOKENS = {
    "veg", "vegetarian", "vegetable", "paneer", "gobhi", "mushroom",
    "kathirikai", "eggplant", "brinjal", "aubergine", "cauliflower", "tofu",
}
# Large enough to sink a meat item below any real vegetarian match, small
# enough that it still shows up last if the menu has nothing else.
_VEG_CONFLICT_PENALTY = 35.0


def _normalize(text: str) -> str:
    """Lowercase, drop parentheticals/punctuation/filler, fold transliterations."""
    text = text.lower()
    text = re.sub(r"\([^)]*\)", " ", text)  # "(V)", "(2Pcs)", "(1 Pc Side)"
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [_VARIANT_MAP.get(t, t) for t in text.split() if t not in FILLER]
    return " ".join(tokens)


def _is_non_veg(raw_name: str, normalized_name: str) -> bool:
    """Whether an item is meat, judged from the raw menu name.

    The markers live in parentheses -- "Seeraga Samba Veg Biryani (V)" versus
    "Chennai Style Plain Biryani (Non Veg)" -- and _normalize strips those, so
    this has to read the original string. Without it a request for a vegetarian
    biryani ranks the Non Veg plain biryani first, since the two names are
    otherwise nearly identical.
    """
    low = raw_name.lower().replace("-", " ")
    if "non veg" in low:
        return True
    if "(v)" in low:
        return False
    return bool(set(normalized_name.split()) & MEAT_TOKENS)


def _expand(normalized: str) -> list:
    """The query plus one alias substitution per variant, original first.

    Scoring takes the best score any variant achieves for a given item, so an
    alias can only ever help an item's rank -- it never suppresses a literal
    match. "goat biryani" still finds the goat biryani at 100; the "mutton"
    variant just also surfaces the mutton ones for the agent to ask about.
    """
    variants = [normalized]
    for phrase, alts in ALIASES.items():
        pattern = rf"\b{re.escape(phrase)}\b"
        if not re.search(pattern, normalized):
            continue
        for alt in alts:
            variants.append(re.sub(pattern, alt, normalized))
    return variants[:_MAX_QUERY_VARIANTS]


class Session:
    """Holds the cart + client for one call. Module-level default below."""

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
        # Every word that appears in any item name, for the unknown-word check.
        self._vocab = {tok for name in self._choices for tok in name.split()}
        # Read once per call: staff toggling mid-call shouldn't change the
        # menu under a caller who is halfway through ordering.
        self.unavailable = availability.load()


_session = Session()


def reset(client=None):
    """Start a fresh call. Tests use this for isolation."""
    global _session
    _session = Session(client=client)
    return _session


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


# -- the seven tools --------------------------------------------------------


def search_menu(query: str) -> dict:
    """Fuzzy-match spoken text against item names. Never guesses between ties."""
    normalized = _normalize(query or "")
    if not normalized.strip():
        return {"query": query, "candidates": [], "ambiguous": False}

    variants = _expand(normalized)

    # Section hint reads the expanded tokens too, so "chai" reaches the
    # Hot Beverages hint that only lists "tea".
    tokens = {t for v in variants for t in v.split()}
    hint = next((section for t, section in GROUP_HINTS.items() if t in tokens), None)

    # Best score any phrasing achieves for each item name.
    choices = list(_session._choices.keys())
    best = {}
    for variant in variants:
        for name, score, _ in process.extract(
            variant,
            choices,
            scorer=fuzz.WRatio,
            limit=20,
            score_cutoff=_SCORE_CUTOFF,
        ):
            if score > best.get(name, 0.0):
                best[name] = score

    # Only the words the caller actually said count as a veg request, not the
    # alias expansions -- otherwise "egg dosa" would expand to "muttai" and
    # nothing would ever trip it.
    veg_request = bool(set(normalized.split()) & VEG_REQUEST_TOKENS)

    candidates = []
    for name, score in best.items():
        for guid in _session._choices[name]:
            item = _session.cart.items[guid]
            if hint and hint in item["groupName"]:
                score += _GROUP_BOOST
            if veg_request and _is_non_veg(item["name"], name):
                score -= _VEG_CONFLICT_PENALTY
            candidates.append(
                {
                    "item_guid": guid,
                    "name": item["name"],
                    "spoken_name": item.get("spoken_name", item["name"]),
                    "price": item["price"],
                    "spoken_price": spoken_price(item["price"]),
                    "available": guid not in _session.unavailable,
                    "menu_group": item["groupName"],
                    "description": item["description"],
                    "required_modifier_groups": _required_groups(item),
                    "score": round(score, 1),
                }
            )

    candidates.sort(key=lambda c: -c["score"])
    candidates = candidates[:_MAX_CANDIDATES]

    # Asked for something they don't make? Say so, rather than offering the
    # nearest string match. Without this, "beef curry" answers with Pepper
    # Chicken Curry and "naan" answers with Naattu Kozhi Rasam -- both of which
    # sound to a caller like the restaurant is substituting on them.
    unmatched = [
        tok
        for tok in normalized.split()
        if len(tok) >= 3 and tok not in _session._vocab and tok not in _ALIAS_TOKENS
    ]
    if unmatched and (not candidates or candidates[0]["score"] < _CONFIDENT_SCORE):
        return {
            "query": query,
            "candidates": [],
            "ambiguous": False,
            "unmatched_terms": unmatched,
        }

    ambiguous = (
        len(candidates) > 1
        and (candidates[0]["score"] - candidates[1]["score"]) < _AMBIGUITY_MARGIN
    )
    return {"query": query, "candidates": candidates, "ambiguous": ambiguous}


def add_item(item_guid: str, qty: int = 1) -> dict:
    # Refused here as well as flagged in search_menu: the agent can call
    # add_item straight from an earlier search result, and an 86'd item must
    # not make it into a cart by that route.
    if item_guid in _session.unavailable:
        item = _session.cart.items.get(item_guid, {})
        name = item.get("spoken_name") or item.get("name", "that")
        return {
            "ok": False,
            "error": "UNAVAILABLE",
            "message": f"The kitchen is out of {name} today.",
        }
    try:
        line_id = _session.cart.add(item_guid, qty)
    except CartError as e:
        return {"ok": False, "error": "INVALID_ITEM", "message": str(e)}
    item = _session.cart.items[item_guid]
    return {
        "ok": True,
        "line_id": line_id,
        "name": item["name"],
        "spoken_name": item.get("spoken_name", item["name"]),
        "qty": qty,
        "required_modifier_groups": _required_groups(item),
    }


def set_modifier(line_id: str, group_guid: str, option_guid: str) -> dict:
    try:
        _session.cart.set_modifier(line_id, group_guid, option_guid)
    except CartError as e:
        return {"ok": False, "error": "INVALID_MODIFIER", "message": str(e)}
    return {"ok": True, **get_cart()}


def remove_item(line_id: str) -> dict:
    """Drop a line. "Actually, take the pappad off"."""
    try:
        _session.cart.remove(line_id)
    except CartError as e:
        return {"ok": False, "error": "INVALID_LINE", "message": str(e)}
    return {"ok": True, **get_cart()}


def set_quantity(line_id: str, qty: int) -> dict:
    """Change how many of one line. To remove, use remove_item."""
    try:
        _session.cart.set_qty(line_id, qty)
    except CartError as e:
        return {"ok": False, "error": "INVALID_LINE", "message": str(e)}
    return {"ok": True, **get_cart()}


def get_cart() -> dict:
    return _session.cart.snapshot()


def submit_order(readback_confirmed: bool = False) -> dict:
    """Submit to Toast. Refuses (structured, never raises) if the order isn't ready."""
    cart = _session.cart

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

    if readback_confirmed:
        cart.readback_confirmed = True
    if not cart.readback_confirmed:
        return {
            "ok": False,
            "error": "READBACK_REQUIRED",
            "message": "Let me read the order back to you before I send it.",
            "cart": cart.snapshot(),
        }

    response = _session.client.create_order(
        cart=cart.snapshot(), customer={}, idempotency_key=_session.order_key
    )
    return {"ok": True, **response}


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
