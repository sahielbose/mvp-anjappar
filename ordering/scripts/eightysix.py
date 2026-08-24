"""Mark items 86'd, or put them back. Stand-in for Toast's stock API.

    uv run python -m ordering.scripts.eightysix                 # show the list
    uv run python -m ordering.scripts.eightysix out goat biryani
    uv run python -m ordering.scripts.eightysix back goat biryani
    uv run python -m ordering.scripts.eightysix clear

Takes the same fuzzy search the agent uses, so you can type what you'd say.
The next call picks the change up; a call already in progress does not.
"""

import sys

from .. import availability
from .. import tools as ordering_tools


def _resolve(words):
    query = " ".join(words)
    result = ordering_tools.search_menu(query)
    candidates = result["candidates"]
    if not candidates:
        print(f"no menu item matches {query!r}")
        return None
    if len(candidates) > 1 and result["ambiguous"]:
        print(f"{query!r} is ambiguous — be more specific:")
        for c in candidates[:5]:
            print(f"  - {c['name']}")
        return None
    return candidates[0]


def _show():
    out = availability.load()
    if not out:
        print("everything is available")
        return
    print(f"86'd ({len(out)}):")
    for guid in out:
        item = ordering_tools._session.cart.items.get(guid)
        print(f"  - {item['name'] if item else guid}")


def main(argv):
    if not argv or argv[0] in ("list", "show"):
        _show()
        return 0

    command, rest = argv[0], argv[1:]

    if command == "clear":
        availability.save(set())
        print("cleared — everything is available")
        return 0

    if command not in ("out", "back") or not rest:
        print(__doc__)
        return 1

    item = _resolve(rest)
    if not item:
        return 1

    availability.mark(item["item_guid"], unavailable=(command == "out"))
    state = "86'd" if command == "out" else "available again"
    print(f"{item['name']} is now {state}")
    # Reload so a dry run in the same process sees it.
    ordering_tools.reset()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
