"""Derive Deepgram nova-3 keyterms from menu.json.

Deepgram wants one `keyterm=` parameter per term. Comma-joining the values
("keyterm=a,b,c") is silently accepted by the API and boosts nothing, so
build_query always emits repeated params and joins multi-word phrases with '+'.
"""

import json
import re
from pathlib import Path

MENU_PATH = Path(__file__).parent / "menu.json"

TOP_N = 100

# Deepgram rejects the whole request once the keyterms exceed 500 tokens:
# "Keyterm limit exceeded. The maximum number of tokens across all keyterms
# is 500." That is a connection error mid-call, not a degraded transcript, so
# the budget is enforced here rather than discovered on a live line.
# https://developers.deepgram.com/docs/keyterm
TOKEN_BUDGET = 380

# Words nova-3 already handles. Not worth a keyterm slot.
COMMON = {
    "chicken", "rice", "fried", "hot", "sour", "soup", "egg", "eggs", "mixed",
    "vegetable", "vegetables", "veg", "with", "and", "the", "of", "a", "style",
    "plain", "classic", "crispy", "sweet", "corn", "baby", "whole", "butter",
    "dry", "gravy", "cold", "drinks", "meal", "side", "sides", "pc", "pcs",
    "piece", "pieces", "tender", "fresh", "warm", "black", "red", "green",
    "blueberry", "water", "juice", "lemonade", "milk", "mint", "cooler",
    "coffee", "tea", "diet", "coke", "sprite", "soda", "curry", "roast",
    "fry", "chili", "chilli", "pepper", "garlic", "potato", "mushroom",
    "seafood", "fish", "crab", "prawn", "shrimp", "goat", "mutton", "paneer",
    "yogurt", "non", "oz", "cola", "lassi", "stuffed", "spiced", "pudding",
    "ice", "cream", "traditional", "signature", "dishes", "main", "course",
    "small", "cravings", "steamed", "food", "south", "indian", "breads",
    "chinese", "desserts", "beverages", "full", "mango", "onion", "coconut",
}

# Orthographic markers that make a token hard for an English-tuned ASR.
MARKERS = ("zh", "aa", "ee", "oo", "ai", "kk", "pp", "tt", "dh", "th",
           "ndu", "mbu", "yam", "adu", "azh", "uzh")


def _clean(text: str) -> str:
    text = re.sub(r"\([^)]*\)", " ", text)      # "(V)", "(2Pcs)"
    text = re.sub(r"[^A-Za-z\s]", " ", text)    # digits, '/', apostrophes
    return " ".join(text.split())


def _token_score(token: str) -> int:
    t = token.lower()
    if t in COMMON or len(t) < 3:
        return 0
    score = sum(2 for m in MARKERS if m in t)
    score += len(t) // 3
    if t.endswith(("am", "ai", "u", "an")):
        score += 2
    return score


def _phrase_score(phrase: str) -> int:
    tokens = phrase.split()
    scores = [_token_score(t) for t in tokens]
    if not any(scores):
        return 0
    # Multi-word dish names are worth more than a bare token: they give
    # Deepgram the whole acoustic shape to bias toward.
    return sum(scores) + (2 * (len(tokens) - 1))


def extract_terms(menu: dict) -> list:
    """Ranked candidate keyterms, most confusable first."""
    scored = {}

    for group in menu["menuGroups"]:
        for item in group["items"]:
            name = _clean(item["name"])
            if not name:
                continue

            # The full dish name, when it carries any Tamil-ish token.
            if len(name.split()) > 1:
                s = _phrase_score(name)
                if s > 0:
                    scored[name] = max(scored.get(name, 0), s)

            # Individual hard tokens.
            for token in name.split():
                s = _token_score(token)
                if s > 0:
                    key = token.capitalize() if token.islower() else token
                    scored[key] = max(scored.get(key, 0), s)

    # Highest score first, then alphabetical so the output is stable run to run.
    return [t for t, _ in sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))]


def build_query(terms: list) -> str:
    """Repeated keyterm= params, spaces as '+'. Never comma-joined."""
    return "&".join("keyterm=" + "+".join(t.split()) for t in terms)


def estimate_tokens(phrase: str) -> int:
    """Deliberately pessimistic token count for one keyterm.

    Deepgram doesn't publish its tokenizer, and these are exactly the strings a
    BPE vocabulary handles worst — "Kozhukattai" and "Saappadu" fragment far
    more than an English word of the same length. Assuming ~1 token per 3
    characters overestimates for English and lands closer to the truth for the
    transliterations, which is the direction we want to be wrong in.
    """
    dense = len(phrase.replace(" ", ""))
    return max(len(phrase.split()), -(-dense // 3))


def budgeted(path=MENU_PATH, budget=TOKEN_BUDGET, top_n=TOP_N) -> list:
    """Highest-ranked keyterms that fit inside the token budget.

    Skips rather than stops on an over-budget term: the list is ranked, but a
    long phrase near the cap shouldn't shut out the shorter terms behind it.
    """
    chosen, spent = [], 0
    for term in extract_terms(load_menu(path))[:top_n]:
        cost = estimate_tokens(term)
        if spent + cost > budget:
            continue
        chosen.append(term)
        spent += cost
    return chosen


def load_menu(path=MENU_PATH) -> dict:
    return json.loads(Path(path).read_text())


def keyterms(path=MENU_PATH, top_n=TOP_N) -> list:
    return extract_terms(load_menu(path))[:top_n]


if __name__ == "__main__":
    terms = keyterms()
    print(f"# {len(terms)} keyterms")
    print(build_query(terms))
