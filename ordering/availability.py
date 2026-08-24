"""What the kitchen has run out of, right now.

The spec calls this make-or-break, and it is: an agent that sells a caller a
biryani the kitchen ran out of is worse than no agent, because someone drives
over for food that isn't there.

This is a deliberate placeholder for Toast's stock API (`stock:read`, plus
`stock:write` to push an 86 back). We can't reach either until Toast grants
order-write access, so until then the list is a local file that staff can
toggle. The read shape here matches what the Toast stock API returns, so
swapping the source later is a change to `load()` and nothing else.

Everything defaults to available. The menu was transcribed on 2026-08-22 with
16 items showing OUT OF STOCK, and those are deliberately NOT baked in --
stock moves daily, and an agent refusing to sell an item the kitchen actually
has is the same failure in the other direction.
"""

import json
from pathlib import Path

EIGHTYSIX_PATH = Path(__file__).parent / "eightysixed.json"


def load(path=EIGHTYSIX_PATH) -> set:
    """GUIDs the kitchen is currently out of. Missing file means all available."""
    path = Path(path)
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        # A half-written file must not take the phone line down mid-service.
        return set()
    return set(data.get("unavailable", []))


def save(guids, path=EIGHTYSIX_PATH) -> None:
    Path(path).write_text(json.dumps({"unavailable": sorted(guids)}, indent=2))


def mark(guid, unavailable=True, path=EIGHTYSIX_PATH) -> set:
    """86 an item, or put it back. Returns the updated set."""
    current = load(path)
    current.add(guid) if unavailable else current.discard(guid)
    save(current, path)
    return current
