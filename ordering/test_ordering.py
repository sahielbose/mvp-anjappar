import json
from pathlib import Path

import pytest

from . import build_menu, tools
from .speech import spoken_price
from .toast_client import ToastClient


@pytest.fixture
def session(tmp_path):
    """Fresh cart + isolated orders dir per test."""
    return tools.reset(client=ToastClient(orders_dir=tmp_path / "orders"))


def guid_for(session, name):
    for guid, item in session.cart.items.items():
        if item["name"] == name:
            return guid
    raise AssertionError(f"no menu item named {name!r}")


def names(result):
    return [c["name"] for c in result["candidates"]]


# -- happy paths ------------------------------------------------------------


def test_simple_one_item_order_end_to_end(session):
    found = tools.search_menu("masala dosa")
    assert found["candidates"][0]["name"] == "Masala Dosa (V)"

    added = tools.add_item(found["candidates"][0]["item_guid"])
    assert added["ok"] is True
    assert added["required_modifier_groups"] == []

    cart = tools.get_cart()
    assert cart["subtotal"] == 12.00
    assert cart["unfilled_required"] == []

    result = tools.submit_order(readback_confirmed=True)
    assert result["ok"] is True
    assert result["status"] == "SUBMITTED"
    assert result["orderGuid"]


def test_required_modifier_filled_correctly(session):
    added = tools.add_item(guid_for(session, "Ceylon Parotta"))
    line_id = added["line_id"]

    group = added["required_modifier_groups"][0]
    assert group["group_name"] == "Filling"

    chicken = next(o for o in group["options"] if o["name"] == "Chicken")
    tools.set_modifier(line_id, group["group_guid"], chicken["option_guid"])

    cart = tools.get_cart()
    assert cart["unfilled_required"] == []
    assert cart["lines"][0]["modifiers"][0]["option_name"] == "Chicken"

    assert tools.submit_order(readback_confirmed=True)["ok"] is True


def test_paid_modifier_adds_to_subtotal(session):
    added = tools.add_item(guid_for(session, "Rava Dosa (V)"))
    item = session.cart.items[guid_for(session, "Rava Dosa (V)")]

    group = item["modifierGroups"][0]
    assert group["isRequired"] is False
    tools.set_modifier(added["line_id"], group["guid"], group["options"][0]["guid"])

    assert tools.get_cart()["subtotal"] == 14.50  # 12.50 + 2.00


# -- submit refusals --------------------------------------------------------


def test_submit_blocked_when_required_modifier_missing(session):
    tools.add_item(guid_for(session, "Ceylon Parotta"))

    result = tools.submit_order(readback_confirmed=True)
    assert result["ok"] is False
    assert result["error"] == "MISSING_MODIFIERS"
    assert result["unfilled_required"][0]["group_name"] == "Filling"
    assert "filling" in result["message"].lower()


def test_submit_blocked_before_readback(session):
    tools.add_item(guid_for(session, "Masala Dosa (V)"))

    result = tools.submit_order()
    assert result["ok"] is False
    assert result["error"] == "READBACK_REQUIRED"

    assert tools.submit_order(readback_confirmed=True)["ok"] is True


def test_submit_blocked_on_empty_cart(session):
    result = tools.submit_order(readback_confirmed=True)
    assert result["ok"] is False
    assert result["error"] == "EMPTY_CART"


def test_editing_cart_invalidates_readback(session):
    tools.add_item(guid_for(session, "Masala Dosa (V)"))
    session.cart.readback_confirmed = True

    tools.add_item(guid_for(session, "Pappad"))

    assert tools.submit_order()["error"] == "READBACK_REQUIRED"


# -- cart edits -------------------------------------------------------------


def test_remove_item_mid_order_updates_total(session):
    tools.add_item(guid_for(session, "Masala Dosa (V)"))  # 12.00
    pappad = tools.add_item(guid_for(session, "Pappad"))  # 2.00
    assert tools.get_cart()["subtotal"] == 14.00

    session.cart.remove(pappad["line_id"])

    cart = tools.get_cart()
    assert cart["subtotal"] == 12.00
    assert len(cart["lines"]) == 1


def test_quantity_change_on_existing_line(session):
    added = tools.add_item(guid_for(session, "Masala Dosa (V)"))
    assert tools.get_cart()["subtotal"] == 12.00

    session.cart.set_qty(added["line_id"], 3)

    cart = tools.get_cart()
    assert cart["lines"][0]["qty"] == 3
    assert cart["subtotal"] == 36.00


def test_line_ids_let_the_agent_refer_to_the_second_dosa(session):
    first = tools.add_item(guid_for(session, "Masala Dosa (V)"))
    second = tools.add_item(guid_for(session, "Masala Dosa (V)"))
    assert first["line_id"] != second["line_id"]

    session.cart.set_qty(second["line_id"], 2)
    lines = {line["line_id"]: line["qty"] for line in tools.get_cart()["lines"]}
    assert lines[first["line_id"]] == 1
    assert lines[second["line_id"]] == 2


# -- fuzzy matching ---------------------------------------------------------


def test_fuzzy_chettinaad_chicken(session):
    assert names(tools.search_menu("chettinaad chicken"))[0] == "Chettinad Chicken Masala"


def test_fuzzy_kotu_parota(session):
    result = names(tools.search_menu("kotu parota"))
    assert "Crispy Crab Kothu Parotta" in result
    # The menu also has a plain "Kothu Parotta", which is the closer literal
    # match and ranks first. The agent disambiguates; search does not guess.
    assert result[0] == "Kothu Parotta"


def test_fuzzy_transliteration_variants(session):
    assert names(tools.search_menu("masala thosai"))[0] == "Masala Dosa (V)"
    assert names(tools.search_menu("gobi 65"))[0] == "Gobhi 65 (V)"
    assert names(tools.search_menu("poondu kuzhambu"))[0] == "Poondu Kulumbu (V)"


def test_ambiguous_aappam_returns_both_variants(session):
    result = tools.search_menu("aappam")
    matched = names(result)

    assert "Aappam (V)" in matched
    assert "Aappam (1 Pc Side)" in matched
    assert result["ambiguous"] is True

    both = [c for c in result["candidates"] if c["name"] in ("Aappam (V)", "Aappam (1 Pc Side)")]
    assert len({c["item_guid"] for c in both}) == 2
    assert {c["menu_group"] for c in both} == {
        "நீராவி உணவு Neeraavi Unavu / Steamed Food",
        "பக்கம் Pakkam / Sides",
    }


def test_ambiguous_idli_returns_both_variants(session):
    matched = names(tools.search_menu("idli"))
    assert "Idli (V)" in matched
    assert "Idli - Pc" in matched


def test_nonexistent_item_returns_no_candidates(session):
    assert tools.search_menu("zzzqwerty nonsense")["candidates"] == []
    assert tools.search_menu("")["candidates"] == []


def test_bad_guid_returns_structured_error_not_exception(session):
    result = tools.add_item("not-a-real-guid")
    assert result["ok"] is False
    assert result["error"] == "INVALID_ITEM"


# -- idempotency ------------------------------------------------------------


def test_same_idempotency_key_creates_one_order(session, tmp_path):
    client = session.client
    cart = {"lines": [], "subtotal": 0}

    first = client.create_order(cart, {}, "key-123")
    second = client.create_order(cart, {}, "key-123")

    assert first["orderGuid"] == second["orderGuid"]
    assert len(list(client.orders_dir.glob("*.json"))) == 1


def test_double_submit_does_not_duplicate_order(session):
    tools.add_item(guid_for(session, "Masala Dosa (V)"))

    first = tools.submit_order(readback_confirmed=True)
    second = tools.submit_order(readback_confirmed=True)

    assert first["orderGuid"] == second["orderGuid"]
    assert len(list(session.client.orders_dir.glob("*.json"))) == 1


# -- menu shape -------------------------------------------------------------


def test_menu_has_all_fourteen_sections(session):
    assert len(session.menu["menuGroups"]) == 14


def test_required_groups_have_sane_bounds(session):
    for item in session.cart.items.values():
        for group in item["modifierGroups"]:
            assert group["guid"]
            assert group["options"]
            if group["isRequired"]:
                assert group["minSelections"] >= 1
            assert group["maxSelections"] >= group["minSelections"]


# -- 1. deterministic guids -------------------------------------------------


def test_guids_are_deterministic_across_builds():
    assert build_menu.build() == build_menu.build()


def test_checked_in_menu_matches_generator():
    """menu.json must be in sync with build_menu.py, byte-identical guids included."""
    on_disk = json.loads((Path(__file__).parent / "menu.json").read_text())
    assert on_disk == build_menu.build()


def test_guids_are_unique(session):
    seen = set()
    for group in session.menu["menuGroups"]:
        for guid in [group["guid"]] + [i["guid"] for i in group["items"]]:
            assert guid not in seen
            seen.add(guid)


# -- 2. spice level is narrow ----------------------------------------------


def _spice_items(menu):
    return [
        (group["name"], item["name"])
        for group in menu["menuGroups"]
        for item in group["items"]
        for mod in item["modifierGroups"]
        if mod["name"] == "Spice Level"
    ]


def test_spice_level_is_limited_to_curries_and_indian_chinese(session):
    spiced = _spice_items(session.menu)
    assert 20 <= len(spiced) <= 26

    sections = {group for group, _ in spiced}
    assert sections == {
        "முக்கிய உணவு Mukkiya Unavu / Main Course",
        "இந்தியன் சைனீஸ் Indian Chinese",
    }


def test_spice_level_dropped_from_everything_else(session):
    spiced = {name for _, name in _spice_items(session.menu)}
    for gone in [
        "Chicken 65",                        # appetizer
        "Chennai Style Chicken Biryani",     # biryani
        "Kizhi Parotta",                     # bread
        "Podi Idli (V)",                     # steamed
        "Madurai Mutton Kari Dosai",         # dosa
        "Full Saapadu",                      # meal
        "Crispy Crab Kothu Parotta",         # signature
        "Nattukozhi Ghee Roast",             # dry roast, not a gravy
        "Aatu Kari Sukka",                   # dry roast
        "Prawn Thokku",                      # dry stir-fry
    ]:
        assert gone not in spiced


def test_all_indian_chinese_keep_spice(session):
    chinese = next(
        g for g in session.menu["menuGroups"] if "Indian Chinese" in g["name"]
    )
    for item in chinese["items"]:
        assert any(m["name"] == "Spice Level" for m in item["modifierGroups"]), item["name"]


# -- 3. tools 6 and 7 -------------------------------------------------------


def test_remove_item_tool(session):
    tools.add_item(guid_for(session, "Masala Dosa (V)"))
    pappad = tools.add_item(guid_for(session, "Pappad"))
    assert tools.get_cart()["subtotal"] == 14.00

    result = tools.remove_item(pappad["line_id"])

    assert result["ok"] is True
    assert result["subtotal"] == 12.00
    assert len(result["lines"]) == 1


def test_set_quantity_tool(session):
    added = tools.add_item(guid_for(session, "Masala Dosa (V)"))

    result = tools.set_quantity(added["line_id"], 3)

    assert result["ok"] is True
    assert result["lines"][0]["qty"] == 3
    assert result["subtotal"] == 36.00


def test_remove_and_set_quantity_reject_bad_line_ids(session):
    assert tools.remove_item("L99")["error"] == "INVALID_LINE"
    assert tools.set_quantity("L99", 2)["error"] == "INVALID_LINE"

    added = tools.add_item(guid_for(session, "Pappad"))
    assert tools.set_quantity(added["line_id"], 0)["error"] == "INVALID_LINE"


def test_removing_last_line_leaves_submit_blocked_on_empty(session):
    added = tools.add_item(guid_for(session, "Masala Dosa (V)"))
    tools.remove_item(added["line_id"])

    assert tools.get_cart()["subtotal"] == 0
    assert tools.submit_order(readback_confirmed=True)["error"] == "EMPTY_CART"


def test_edits_via_tools_invalidate_readback(session):
    added = tools.add_item(guid_for(session, "Masala Dosa (V)"))
    session.cart.readback_confirmed = True

    tools.set_quantity(added["line_id"], 2)

    assert tools.submit_order()["error"] == "READBACK_REQUIRED"


def test_every_tool_has_a_schema():
    named = {s["function"]["name"] for s in tools.TOOL_SCHEMAS}
    assert named == {
        "search_menu", "add_item", "set_modifier", "remove_item",
        "set_quantity", "get_cart", "submit_order",
    }
    for schema in tools.TOOL_SCHEMAS:
        fn = schema["function"]
        assert fn["description"]
        assert fn["parameters"]["type"] == "object"
        assert callable(getattr(tools, fn["name"]))


# -- 4. spoken prices and names --------------------------------------------


@pytest.mark.parametrize(
    "amount,expected",
    [
        (17.00, "seventeen dollars"),
        (12.50, "twelve dollars and fifty cents"),
        (1.00, "one dollar"),
        (2.50, "two dollars and fifty cents"),
        (33.00, "thirty three dollars"),
        (14.50, "fourteen dollars and fifty cents"),
        (0.00, "no extra charge"),
        (0.75, "seventy five cents"),
        (108.25, "one hundred eight dollars and twenty five cents"),
    ],
)
def test_spoken_price(amount, expected):
    assert spoken_price(amount) == expected


def test_cart_returns_spoken_prices_and_subtotal(session):
    tools.add_item(guid_for(session, "Rava Dosa (V)"))  # 12.50
    tools.add_item(guid_for(session, "Pappad"))         # 2.00

    cart = tools.get_cart()

    assert cart["spoken_subtotal"] == "fourteen dollars and fifty cents"
    spoken = {line["name"]: line["spoken_price"] for line in cart["lines"]}
    assert spoken["Rava Dosa (V)"] == "twelve dollars and fifty cents"
    assert spoken["Pappad"] == "two dollars"


def test_search_returns_spoken_prices(session):
    top = tools.search_menu("karaikudi pepper crab")["candidates"][0]
    assert top["price"] == 33.00
    assert top["spoken_price"] == "thirty three dollars"


def test_items_with_digits_have_a_spoken_name(session):
    for item in session.cart.items.values():
        if any(ch.isdigit() for ch in item["name"]):
            assert "spoken_name" in item, item["name"]


def test_spoken_names_are_speakable(session):
    """No digits, slashes, or parentheses survive into a spoken name."""
    for item in session.cart.items.values():
        spoken = item.get("spoken_name")
        if spoken:
            assert not any(ch.isdigit() for ch in spoken), item["name"]
            assert "/" not in spoken and "(" not in spoken, item["name"]


def test_specific_spoken_names(session):
    by_name = {i["name"]: i for i in session.cart.items.values()}
    assert by_name["Gobhi 65 (V)"]["spoken_name"] == "gobi sixty five"
    assert by_name["Chicken 65"]["spoken_name"] == "chicken sixty five"
    assert by_name["Poondu Kulumbu (V)"]["spoken_name"] == "poondu kuzhambu"
    assert by_name["Hakka Chili (Dry / Gravy)"]["spoken_name"] == "hakka chili, dry or gravy"


def test_cart_lines_carry_spoken_name(session):
    tools.add_item(guid_for(session, "Chicken 65"))
    assert tools.get_cart()["lines"][0]["spoken_name"] == "chicken sixty five"


def test_cart_lines_carry_spoken_quantity(session):
    """Quantity is a number the agent has to say out loud, so it gets a spoken form."""
    added = tools.add_item(guid_for(session, "Pappad"), qty=3)
    line = tools.get_cart()["lines"][0]
    assert line["qty"] == 3
    assert line["spoken_qty"] == "three"

    tools.set_quantity(added["line_id"], 12)
    assert tools.get_cart()["lines"][0]["spoken_qty"] == "twelve"


def test_no_bare_digits_in_anything_the_agent_speaks(session):
    """Every *spoken_* field must be free of digits."""
    tools.add_item(guid_for(session, "Chicken 65"), qty=2)
    cart = tools.get_cart()

    spoken = [cart["spoken_subtotal"]]
    for line in cart["lines"]:
        spoken += [line["spoken_name"], line["spoken_price"], line["spoken_qty"]]
    for c in tools.search_menu("gobi 65")["candidates"]:
        spoken += [c["spoken_name"], c["spoken_price"]]

    for text in spoken:
        assert not any(ch.isdigit() for ch in text), text


# -- 5. search capping and ranking -----------------------------------------


def test_search_caps_at_five_candidates(session):
    result = tools.search_menu("chicken")
    assert len(result["candidates"]) <= 5
    assert result["ambiguous"] is True


def test_a_dosa_ranks_dosas_above_other_items(session):
    candidates = tools.search_menu("a dosa")["candidates"]
    assert candidates
    for c in candidates:
        assert "Thosai / Dosai" in c["menu_group"], c["name"]


def test_group_hint_rescues_a_query_bare_matching_gets_wrong(session):
    """Without the section boost, the "Egg" side and the egg biryanis outrank
    every actual dosa for this phrasing. This is what the boost is for."""
    result = tools.search_menu("egg dosa")

    assert result["candidates"]
    assert "Egg" not in names(result)
    for c in result["candidates"]:
        assert "Thosai / Dosai" in c["menu_group"], c["name"]


def test_group_hint_does_not_break_specific_queries(session):
    assert names(tools.search_menu("masala dosa"))[0] == "Masala Dosa (V)"
    assert names(tools.search_menu("mutton biryani"))[0] == "Chennai Style Mutton Biryani"


def test_clear_winner_is_not_ambiguous(session):
    assert tools.search_menu("chettinaad chicken")["ambiguous"] is False
    assert tools.search_menu("karaikudi pepper crab")["ambiguous"] is False


def test_no_candidates_is_not_ambiguous(session):
    result = tools.search_menu("zzzqwerty nonsense")
    assert result["candidates"] == []
    assert result["ambiguous"] is False


# -- 6. sides that collide with full items ----------------------------------


@pytest.mark.parametrize(
    "query,side,main",
    [
        ("parotta", "Parotta (1 Pc Side)", "Parotta (2Pcs)"),
        ("chapathi", "Chapathi (1 Pc Side)", "Chapathi (2Pcs)"),
        ("idiyappam", "Idiyappam (2 Pc Side)", "Idiyappam (V)"),
        ("aappam", "Aappam (1 Pc Side)", "Aappam (V)"),
    ],
)
def test_side_and_main_both_returned(session, query, side, main):
    result = tools.search_menu(query)
    matched = names(result)

    assert side in matched, f"{query}: missing side portion"
    assert main in matched, f"{query}: missing full item"
    assert result["ambiguous"] is True

    pair = [c for c in result["candidates"] if c["name"] in (side, main)]
    assert len({c["item_guid"] for c in pair}) == 2
    assert len({c["menu_group"] for c in pair}) == 2
    assert len({c["price"] for c in pair}) == 2


def test_colliding_side_names_are_distinguishable(session):
    """The four sides must not share a written name with their full-size item."""
    names_by_group = {}
    for item in session.cart.items.values():
        names_by_group.setdefault(item["name"], set()).add(item["groupName"])
    for name, groups in names_by_group.items():
        assert len(groups) == 1, f"{name} appears in {groups}"
