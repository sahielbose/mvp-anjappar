"""Every dish on the menu, the way callers actually ask for it.

The demo is the manager saying whatever he likes down a phone line, so recall
across the whole menu matters more than any three showcase dishes.
"""

import json
from pathlib import Path

import pytest

from . import availability
from . import tools as t

MENU = json.loads((Path(__file__).parent / "menu.json").read_text())
ITEMS = [i for g in MENU["menuGroups"] for i in g["items"]]

# The four side-vs-full pairs Toast names identically. Both come back flagged
# ambiguous so the agent asks; neither is expected to rank first.
KNOWN_SECOND = {
    "Parotta (1 Pc Side)",
    "Chapathi (1 Pc Side)",
    "Idiyappam (2 Pc Side)",
    "Aappam (1 Pc Side)",
}


@pytest.fixture(autouse=True)
def fresh():
    t.reset()


@pytest.mark.parametrize("item", ITEMS, ids=[i["name"] for i in ITEMS])
def test_every_item_is_findable_by_its_own_name(item):
    names = [c["name"] for c in t.search_menu(item["name"])["candidates"]]
    assert item["name"] in names
    if item["name"] not in KNOWN_SECOND:
        assert names[0] == item["name"]


# How people actually talk: English words for Tamil dishes, shortened names,
# and the transliterations ASR produces.
@pytest.mark.parametrize(
    "said,expected",
    [
        ("goat curry", "Mutton"),
        ("lamb curry", "Mutton"),
        ("eggplant curry", "Ennai Kathirikai"),
        ("cauliflower", "Gobhi 65"),
        ("garlic curry", "Poondu Kulumbu"),
        ("anchovies", "Nethili Fish Fry"),
        ("king fish fry", "Vanjaram Meen Fry"),
        ("egg dosa", "Muttai Dosa"),
        ("filter coffee", "Kaapi"),
        ("chai", "Masala Tea"),
        ("coke", "Coca-Cola"),
        ("papad", "Pappad"),
        ("chapati", "Chapathi"),
        ("vegetable soup", "Kaikari Soup"),
        ("seruga samba goat biryani", "Seeraga Samba Goat Biryani"),
        ("naatu kozhi rasam", "Naattu Kozhi Rasam"),
        ("mangai meen kolambu", "Mangai Meen Kulambu"),
        ("koozhi paniyaram", "Kuzhi Paniyaram"),
        ("prawn thoku", "Prawn Thokku"),
    ],
)
def test_caller_phrasings_reach_the_right_dish(said, expected):
    names = [c["name"] for c in t.search_menu(said)["candidates"]]
    assert any(expected.lower() in n.lower() for n in names), f"{said!r} -> {names}"


# Asking for something they don't serve must not be answered with a near-miss.
# "beef curry" scoring 85.5 against Pepper Chicken Curry is the case that
# motivated the unmatched-term rule.
@pytest.mark.parametrize(
    "said",
    ["naan", "garlic naan", "butter naan", "samosa", "tandoori chicken",
     "saag paneer", "beef curry", "pork chops", "spring roll", "gulab jamun",
     "vindaloo", "dal makhani", "pizza"],
)
def test_off_menu_requests_return_nothing(said):
    result = t.search_menu(said)
    assert result["candidates"] == [], f"{said!r} -> {result['candidates'][0]['name']}"
    assert result["unmatched_terms"]


def test_a_veg_request_is_never_answered_with_meat():
    for said in ["paneer tikka", "vegetarian biryani", "veg biryani", "vegetable curry"]:
        top = t.search_menu(said)["candidates"][0]
        assert not t._is_non_veg(top["name"], t._normalize(top["name"])), f"{said} -> {top['name']}"


def test_non_veg_requests_are_not_penalised():
    assert t.search_menu("goat biryani")["candidates"][0]["name"] == "Seeraga Samba Goat Biryani"
    assert t.search_menu("chicken biryani")["candidates"][0]["name"] == "Chennai Style Chicken Biryani"
    assert t.search_menu("egg curry")["candidates"][0]["name"] == "Egg Curry"


def test_an_alias_never_outranks_a_literal_match():
    """goat->mutton must surface mutton dishes without displacing the goat one."""
    top = t.search_menu("goat biryani")["candidates"][0]
    assert "Goat" in top["name"]


# -- 86'd items -------------------------------------------------------------


def test_everything_is_available_by_default():
    assert all(c["available"] for c in t.search_menu("biryani")["candidates"])


def test_an_eightysixed_item_is_flagged_and_refused(tmp_path):
    guid = t.search_menu("goat biryani")["candidates"][0]["item_guid"]

    path = tmp_path / "86.json"
    availability.save({guid}, path)
    t._session.unavailable = availability.load(path)

    flagged = next(
        c for c in t.search_menu("goat biryani")["candidates"] if c["item_guid"] == guid
    )
    assert flagged["available"] is False

    result = t.add_item(guid)
    assert result["ok"] is False
    assert result["error"] == "UNAVAILABLE"
    assert t.get_cart()["lines"] == []


def test_a_missing_or_corrupt_86_file_never_takes_the_line_down(tmp_path):
    assert availability.load(tmp_path / "nope.json") == set()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert availability.load(bad) == set()
