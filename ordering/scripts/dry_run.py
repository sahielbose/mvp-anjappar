"""Walk one realistic four-item order end to end, printing every tool call.

Not a test. Read it top to bottom to spot awkward conversational flow before
hearing it over a phone line.

Run: uv run python -m ordering.scripts.dry_run
"""

import tempfile

from .. import tools
from ..toast_client import ToastClient

WIDTH = 76


def rule(char="="):
    print(char * WIDTH)


def caller(line):
    print(f"\nCALLER   “{line}”")


def agent(line):
    print(f"AGENT    “{line}”")


def call(fn, **kwargs):
    """Invoke a tool, print the call and a readable digest of the response."""
    args = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
    print(f"  tool   {fn.__name__}({args})")
    result = fn(**kwargs)
    for line in digest(fn.__name__, result):
        print(f"  ->     {line}")
    return result


def digest(name, result):
    """One tool response, as a few readable lines."""
    if name == "search_menu":
        out = [f"{len(result['candidates'])} candidates, ambiguous={result['ambiguous']}"]
        for c in result["candidates"]:
            section = c["menu_group"].split("/")[-1].strip()
            out.append(f"   {c['spoken_name']}  ({c['spoken_price']})  [{section}]")
            for grp in c["required_modifier_groups"]:
                opts = " / ".join(o["name"] for o in grp["options"])
                out.append(f"      needs {grp['group_name']}: {opts}")
        return out

    if not result.get("ok", True):
        return [f"REFUSED {result['error']}: {result['message']}"]

    if name == "add_item":
        out = [f"line {result['line_id']} = {result['qty']} x {result['spoken_name']}"]
        for grp in result["required_modifier_groups"]:
            opts = " / ".join(o["name"] for o in grp["options"])
            out.append(f"   needs {grp['group_name']}: {opts}")
        return out

    if name == "set_customer_name":
        return [f"name = {result['customer_name']!r}"]

    if name == "submit_order":
        return [
            f"{result['status']}  order {result['orderGuid']}",
            f"pickup code {result['pickupCode']}  spoken: \"{result['spokenPickupCode']}\"",
        ]

    # get_cart, set_modifier, remove_item, set_quantity all return a cart.
    out = []
    for line in result["lines"]:
        mods = ", ".join(m["option_name"] for m in line["modifiers"])
        out.append(
            f"   {line['line_id']}  {line['qty']} x {line['name']}"
            + (f" ({mods})" if mods else "")
            + f"  {line['spoken_price']}"
        )
    out.append(f"   subtotal {result['spoken_subtotal']}")
    if result.get("customer_name"):
        out.append(f"   name {result['customer_name']}")
    if result["unfilled_required"]:
        still = ", ".join(
            f"{u['group_name']} for {u['item_name']}" for u in result["unfilled_required"]
        )
        out.append(f"   still needed: {still}")
    return out


def pick(groups, group_name, option_name):
    """Find (group_guid, option_guid) for a named choice."""
    grp = next(g for g in groups if g["group_name"] == group_name)
    opt = next(o for o in grp["options"] if o["name"] == option_name)
    return grp["group_guid"], opt["option_guid"]


def by_name(session, name):
    return next(g for g, i in session.cart.items.items() if i["name"] == name)


def main():
    session = tools.reset(client=ToastClient(orders_dir=tempfile.mkdtemp()))

    rule()
    print(" Anjappar Dublin — ordering layer dry run".center(WIDTH))
    rule()
    agent("Thanks for calling Anjappar Dublin. What can I get you?")

    # 1. A curry, with the one required choice.
    caller("Hi, can I get a chettinaad chicken?")
    found = call(tools.search_menu, query="chettinaad chicken")
    top = found["candidates"][0]
    agent(f"Sure, the {top['spoken_name']}, that's {top['spoken_price']}. "
          "Would you like that mild, medium or spicy?")

    caller("Medium is fine.")
    added = call(tools.add_item, item_guid=top["item_guid"])
    group_guid, option_guid = pick(added["required_modifier_groups"], "Spice Level", "Medium")
    call(tools.set_modifier, line_id=added["line_id"],
         group_guid=group_guid, option_guid=option_guid)

    # 2. A bread, where the side portion collides with the full item.
    caller("And a couple of parottas.")
    found = call(tools.search_menu, query="parotta")
    agent("Just to check, did you want the two piece parotta at thirteen dollars, "
          "or the single piece side at four dollars?")

    caller("The two piece one, and make it two of those.")
    parotta = call(tools.add_item, item_guid=by_name(session, "Parotta (2Pcs)"), qty=2)

    # 3. A drink, where search comes back ambiguous and the agent must ask.
    caller("What cold drinks do you have? Something with mango.")
    found = call(tools.search_menu, query="mango lassi")
    first, second = found["candidates"][0], found["candidates"][1]
    agent(f"I can do a plain {first['spoken_name']} at {first['spoken_price']}, "
          f"or the {second['spoken_name']}, also {second['spoken_price']}. Which one?")

    caller("The mango one.")
    call(tools.add_item, item_guid=by_name(session, "Mango Kulukki Sarbath"))

    # 4. A mid-order addition the caller then changes their mind about.
    caller("Oh, throw in a pappad too.")
    pappad = call(tools.add_item, item_guid=by_name(session, "Pappad"))

    caller("Actually, no, drop the pappad. But make the parottas three.")
    call(tools.remove_item, line_id=pappad["line_id"])
    call(tools.set_quantity, line_id=parotta["line_id"], qty=3)

    # 5. Dessert.
    caller("And something sweet to finish.")
    found = call(tools.search_menu, query="elaneer payasam")
    dessert = found["candidates"][0]
    call(tools.add_item, item_guid=dessert["item_guid"])

    # 6. The agent tries to submit before it has a name.
    caller("That's everything, send it.")
    call(tools.submit_order)

    # 7. Name, asked at the end rather than up front, and misheard once.
    agent("Sure. Can I get a first name for the order?")

    caller("It's Priya.")
    call(tools.set_customer_name, name="Prea")
    agent("Got it, Prea.")

    caller("No, Priya. P R I Y A.")
    call(tools.set_customer_name, name="Priya")

    # 8. Readback, then submit for real.
    cart = call(tools.get_cart)
    # "3 parotta, two pieces" is genuinely ambiguous out loud, so anything with
    # a quantity above one gets "N orders of ...". See NOTES.md.
    spoken = "; ".join(
        (f"{line['spoken_qty']} orders of {line['spoken_name']}" if line["qty"] > 1
         else f"one {line['spoken_name']}")
        + (f", {line['modifiers'][0]['option_name'].lower()}" if line["modifiers"] else "")
        for line in cart["lines"]
    )
    agent(f"Let me read that back: {spoken}. That comes to {cart['spoken_subtotal']}, "
          f"under the name {cart['customer_name']}. Is that right?")

    caller("Yep, that's right.")
    submitted = call(tools.submit_order, readback_confirmed=True)
    agent(f"Great, that's in. About twenty minutes for pickup. Your pickup code is "
          f"{submitted['spokenPickupCode']}. Give that at the counter.")

    print()
    rule()


if __name__ == "__main__":
    main()
