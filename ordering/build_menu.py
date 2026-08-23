"""Generate menu.json in Toast Menus V3 shape.

Item names, prices and descriptions were transcribed from the live Toast
ordering page on 2026-08-22:
https://order.toasttab.com/online/anjappar-dublin-summit-3996-summit-road

Modifier groups are NOT visible on that page without clicking into each item,
so every modifier group below is inferred from the item description or invented.
See NOTES.md for the full list of assumptions.

GUIDs are uuid5-derived from stable keys, so re-running this produces a
byte-identical file. Never switch these back to uuid4: the agent hands guids to
the caller mid-session and a regeneration would invalidate them.

Run: uv run python ordering/build_menu.py
"""

import json
import re
import uuid
from pathlib import Path

# Fixed namespace. Changing this rotates every guid in the menu.
NS = uuid.UUID("6f1d4a52-1c3e-5b8a-9d21-3f7c0e4a8b66")


def g(*parts) -> str:
    """Deterministic guid from a stable key."""
    return str(uuid.uuid5(NS, "|".join(parts)))


# name, options, isRequired, minSelections, maxSelections
# options are str or (str, price)
MODS = {
    # Required choices implied by the menu description.
    "spice": ("Spice Level", ["Mild", "Medium", "Spicy"], True, 1, 1),
    "protein_vcm": ("Protein", ["Vegetable", "Chicken", "Mutton"], True, 1, 1),
    "protein_vecm": ("Protein", ["Vegetable", "Egg", "Chicken", "Mutton"], True, 1, 1),
    "protein_vec": ("Protein", ["Vegetable", "Egg", "Chicken"], True, 1, 1),
    "protein_pcs": ("Protein", ["Paneer", "Chicken", "Shrimp"], True, 1, 1),
    "protein_gcs": ("Protein", ["Gobhi", "Chicken", "Shrimp"], True, 1, 1),
    "ceylon_filling": ("Filling", ["Plain Egg", "Chicken", "Mutton Kheema"], True, 1, 1),
    "chinese_style": ("Style", ["Dry", "Gravy"], True, 1, 1),
    "asaiva_curry": ("Curry", ["Chettinad Chicken", "Mutton Masala", "Fish Curry"], True, 1, 1),
    # Invented accompaniment groups that make the two Aappam / Idli
    # variants meaningfully different. See NOTES.md.
    "aappam_side": ("Accompaniment", ["Coconut Milk", "Vegetable Stew", "Salna"], True, 1, 1),
    "idli_side": ("Accompaniment", ["Sambar", "Coconut Chutney", "Milagai Podi"], True, 1, 1),
    # Optional paid add-ons.
    "add_potato": ("Add Potato Masala", [("Potato Masala", 2.0)], False, 0, 1),
    "add_egg": ("Add Egg", [("Egg", 2.0)], False, 0, 1),
}


# How the TTS should say a name. Keys are exact item names.
# Required for anything with a digit, a slash, or a transliteration trap.
SPOKEN_NAMES = {
    # Digits.
    "Gobhi 65 (V)": "gobi sixty five",
    "Chicken 65": "chicken sixty five",
    "Prawn 65": "prawn sixty five",
    "Mushroom 65 (V)": "mushroom sixty five",
    "Paneer 65 (V)": "paneer sixty five",
    "Seafood 65": "seafood sixty five",
    "Chennai Style Chicken 65 Biryani": "chennai style chicken sixty five biryani",
    "Seerga Samba Chicken 65 Biryani": "seeraga samba chicken sixty five biryani",
    "Chapathi (2Pcs)": "chapathi, two pieces",
    "Parotta (2Pcs)": "parotta, two pieces",
    "Virudhunagar Parotta (2Pcs)": "viroodhunagar parotta, two pieces",
    "Egg Aappam 2Pcs": "egg appam, two pieces",
    "Sambar 8oz": "sambar, eight ounces",
    "Sambar 16oz": "sambar, sixteen ounces",
    "Rasam 8oz": "rasam, eight ounces",
    "Rasam 16": "rasam, sixteen ounces",
    # Side portions that collide by name with a full item elsewhere.
    "Parotta (1 Pc Side)": "parotta, single piece side",
    "Chapathi (1 Pc Side)": "chapathi, single piece side",
    "Idiyappam (2 Pc Side)": "idiyappam, two piece side",
    "Aappam (1 Pc Side)": "appam, single piece side",
    "Idli - Pc": "idli, single piece side",
    # Slashes read as "slash".
    "Mutton Meal / Kari Saappadu": "mutton meal, kari saappadu",
    "Chicken Meal / Kozhi Saappadu": "chicken meal, kozhi saappadu",
    "Fish Meal / Meen Saappadu": "fish meal, meen saappadu",
    "Veg Meal / Saiva Saappadu (V)": "veg meal, saiva saappadu",
    "Sweet Beeda / Paan": "sweet beeda, or paan",
    "Hakka Chili (Dry / Gravy)": "hakka chili, dry or gravy",
    "Schezwan (Dry / Gravy)": "schezwan, dry or gravy",
    # Transliteration traps.
    "Kuzhi Paniyaram (V)": "koozhi paniyaram",
    "Marina Beach Sundal (V)": "marina beach sundal",
    "Vanjaram Meen Fry": "vanjaram meen fry",
    "Nethili Fish Fry": "nethili fish fry",
    "Mutton Kola Urundai": "mutton kola urundai",
    "Naattu Kozhi Rasam": "nattu kozhi rasam",
    "Aatu Elumbu Soup": "aatu elumbu soup",
    "Kaikari Soup (V)": "kaikari soup",
    "Pomfret Polichathu": "pomfret polichathu",
    "Ennai Kathirikai (V)": "ennai kathirikai",
    "Paneer Therakkal (V)": "paneer therakkal",
    "Poondu Kulumbu (V)": "poondu kuzhambu",
    "Nattukozhi Ghee Roast": "nattu kozhi ghee roast",
    "Chettinad Chicken Masala": "chettinaad chicken masala",
    "Prawn Thokku": "prawn thokku",
    "Karaikudi Pepper Crab": "karaikudi pepper crab",
    "Mangai Meen Kulambu": "mangai meen kuzhambu",
    "Aatu Kari Sukka": "aatu kari sukka",
    "Mutton Kudal Kootu": "mutton kudal kootu",
    "Seeraga Samba Goat Biryani": "seeraga samba goat biryani",
    "Seerga Samba Nattu Kozhi Biryani": "seeraga samba nattu kozhi biryani",
    "Madurai Mutton Kari Dosai": "madurai mutton kari dosai",
    "Idiyappam (V)": "idiyappam",
    "Uthappam (V)": "uthappam",
    "Kizhi Parotta": "kizhi parotta",
    "Paal Kozhukattai": "paal kozhukattai",
    "Elaneer Payasam": "elaneer payasam",
    "Kavuni Rice Pudding": "kavuni rice pudding",
    "Jigarthanda": "jigar thanda",
    "Mango Kulukki Sarbath": "mango kulukki sharbath",
    "Nannari Sharbat": "nannari sharbath",
    "Neer Mor Soda": "neer mor soda",
    "Moru Thatha": "moru thatha",
    "Kumbakonam Degree Kaapi": "kumbakonam degree kaapi",
    "Sukku Malli Kaappi": "sukku malli kaapi",
}


# (name, price, description, [modifier group keys])
#
# Spice Level is deliberately limited to Mukkiya Unavu gravy curries and the
# Indian Chinese section. See NOTES.md for the four Main Course items that were
# excluded and why.
MENU = [
    (
        "கொஞ்சம் Konjam / Small Cravings",
        [
            ("Kuzhi Paniyaram (V)", 12.0, "Fried Dumplings, Dangar Chutney, Malli Chutney", []),
            ("Marina Beach Sundal (V)", 11.0, "Peas, Onion, Coconut, Tomato, Spices", []),
            ("Gobhi 65 (V)", 13.0, "Crispy Fried Cauliflower Marinated with Red Chilli", []),
            (
                "Baby Corn Chili Fry (V)",
                14.0,
                "Crispy Baby Corn with Spicy Tangy Capsicum, Onion and Stirfried Sauce",
                [],
            ),
            (
                "Madurai Fried Chicken",
                16.0,
                '"Poricha kozhi" a street food classic from the night markets of Madurai',
                [],
            ),
            ("Chicken Lollipop", 15.0, "Red Chili Marinated Chicken Wings, Chili Garlic Dip", []),
            ("Chicken 65", 14.0, "Crispy Fried Chicken Nuggets Marinated with Red Chilli", []),
            ("Vanjaram Meen Fry", 17.0, "Wild caught king fish, red chili masala", []),
            ("Nethili Fish Fry", 16.0, "Crispy anchovies, curry leaves, lemon aioli", []),
            ("Mutton Kola Urundai", 16.0, "Crispy meatballs, green mango Moringa chutney", []),
            ("Prawn 65", 18.0, "", []),
            ("Mushroom 65 (V)", 15.0, "", []),
            ("Whole Pomfret Fry", 18.0, "", []),
            ("Paneer 65 (V)", 14.0, "", []),
        ],
    ),
    (
        "சூப் Suup / Soups",
        [
            ("Hot & Sour Soup", 6.0, "", []),
            ("Naattu Kozhi Rasam", 7.0, "Spicy country chicken bone broth, coriander", []),
            ("Aatu Elumbu Soup", 7.0, "Mutton soup, warm spices, lite coconut cream", []),
            ("Kaikari Soup (V)", 6.0, "Mixed Vegetables Broth, Lentils, Curry Leaves", []),
            ("Sweetcorn Soup", 6.0, "", []),
        ],
    ),
    (
        "அசத்தல் Asathal / Signature Dishes",
        [
            ("Crispy Crab Kothu Parotta", 18.0, "Soft shell crab masala, egg, onion raita", []),
            (
                "Paal Parotta",
                17.0,
                "Parotta Drizzled With Coconut Milk Served in Banana Leaf",
                [],
            ),
            (
                "Pomfret Polichathu",
                18.0,
                "Pan Fried Fish wrapped in Banana Leaf with Coconut Milk Masala",
                [],
            ),
            ("Seafood 65", 19.0, "Crab, Fish, Calamari, Shrimp", []),
        ],
    ),
    (
        "முக்கிய உணவு Mukkiya Unavu / Main Course",
        [
            ("Ennai Kathirikai (V)", 13.0, "Stuffed eggplant curry, coconut, red chili tamarind", ["spice"]),
            ("Drumstick Paya (V)", 14.0, "Drumstick Cooked with Coconut", ["spice"]),
            (
                "Seasonal Veg Kuruma (V)",
                13.0,
                "Mixed Vegetables, Marble Potatoes, Cashew Coconut Gravy",
                ["spice"],
            ),
            ("Paneer Therakkal (V)", 14.0, "Flavorful coconut poppy seed curry, fennel seeds", ["spice"]),
            (
                "Poondu Kulumbu (V)",
                14.0,
                "Hot and sour shallot curry, roasted garlic and red chili",
                ["spice"],
            ),
            ("Anjappar Chicken Masala", 15.0, "Our signature dish, chef's special sauce", ["spice"]),
            ("Pepper Chicken Curry", 15.0, "Slow cooked chicken, black pepper coconut sauce", ["spice"]),
            # Dry roast, not a gravy: no spice group.
            (
                "Nattukozhi Ghee Roast",
                16.0,
                "Clarified butter roasted country chicken, dry chettinad masala",
                [],
            ),
            (
                "Chettinad Chicken Masala",
                14.0,
                "Slow cooked chicken, classic signature spice blend",
                ["spice"],
            ),
            # Dry stir-fry, not a gravy: no spice group.
            (
                "Prawn Thokku",
                18.0,
                "Stir-fried prawns, shallot and curry leaf masala, tomatoes",
                [],
            ),
            (
                "Karaikudi Pepper Crab",
                33.0,
                "Dungeness crab, roasted shallots, coconut black pepper masala",
                ["spice"],
            ),
            ("Mangai Meen Kulambu", 16.0, "Fish, green mango, hot and sour curry", ["spice"]),
            # Dry roast ("sukka"): no spice group.
            ("Aatu Kari Sukka", 16.0, "Slow cooked mutton roast, black pepper", []),
            ("Mutton Masala", 16.0, "Slow cooked mutton, classic signature spice blend", ["spice"]),
            ("Mutton Paaya", 15.0, "", ["spice"]),
            # Dry roast: no spice group.
            ("Mutton Liver Roast", 15.0, "", []),
            ("Mutton Kudal Kootu", 15.0, "", ["spice"]),
            ("Butter Chicken", 15.0, "", ["spice"]),
            ("Chicken Tikka Masala", 15.0, "", ["spice"]),
            ("Paneer Butter Masala (V)", 15.0, "", ["spice"]),
            ("Kadai Paneer (V)", 15.0, "", ["spice"]),
            ("Kadai Vegetable (V)", 14.0, "", ["spice"]),
            ("Egg Curry", 13.0, "", ["spice"]),
        ],
    ),
    (
        "பிரியாணி Biryani",
        [
            ("Chennai Style Mutton Biryani", 18.0, "", []),
            ("Chennai Style Chicken Biryani", 16.0, "", []),
            ("Chennai Style Plain Biryani (Non Veg)", 14.0, "", []),
            ("Chennai Style Chicken 65 Biryani", 16.0, "", []),
            ("Chennai Style Egg Biryani (Non Veg)", 15.0, "", []),
            (
                "Seeraga Samba Goat Biryani",
                19.0,
                "Slow cooked bone-in goat, aromatic short grain rice, onion raita",
                [],
            ),
            (
                "Seerga Samba Nattu Kozhi Biryani",
                19.0,
                "Bone-in country chicken, aromatic seeraga samba rice, onion raita",
                [],
            ),
            ("Seerga Samba Chicken Biryani", 19.0, "", []),
            ("Seerga Samba Chicken 65 Biryani", 19.0, "", []),
            ("Seeraga Samba Plain Biryani (Non Veg)", 15.0, "", []),
            ("Seeraga Samba Egg Biryani (Non Veg)", 16.0, "", []),
            ("Seeraga Samba Veg Biryani (V)", 15.0, "", []),
        ],
    ),
    (
        "சாப்பாடு Saappadu / Meal",
        [
            (
                "Mutton Meal / Kari Saappadu",
                18.0,
                "Mutton kola urundai, mutton masala, Rice, Chappathi, Poriyal, Kootu, "
                "Keerai, Rasam, Buttermilk, Sweet, Appalam, Pickle",
                [],
            ),
            (
                "Chicken Meal / Kozhi Saappadu",
                17.0,
                "Chicken'65, chettinad chicken masala, Rice, Chappathi, Poriyal, Kootu, "
                "Keerai, Rasam, Buttermilk, Sweet, Appalam, Pickle",
                [],
            ),
            (
                "Fish Meal / Meen Saappadu",
                17.0,
                "Crispy anchovies, meen kulambu, Rice, Chappathi, Poriyal, Kootu, "
                "Keerai, Rasam, Buttermilk, Sweet, Appalam, Pickle",
                [],
            ),
            (
                "Full Saapadu",
                22.0,
                "Chicken Masala, Mutton Masala, Fish Curry, Egg Fry, Rice, Chappathi, "
                "Poriyal, Kootu, Keerai, Rasam, Buttermilk, Sweet, Appalam, Pickle",
                [],
            ),
            (
                "Veg Meal / Saiva Saappadu (V)",
                15.0,
                "Gobi 65, kuruma, sambar, Kara kulambu, Rice, Chappathi, Poriyal, Kootu, "
                "Keerai, Rasam, Buttermilk, Sweet, Appalam, Pickle",
                [],
            ),
        ],
    ),
    (
        "தோசை Thosai / Dosai",
        [
            ("Masala Dosa (V)", 12.0, "Crepe stuffed with turmeric mash potatoes", []),
            ("Classic Nei Roast Dosa (V)", 12.5, "Clarified butter-roasted crepe", ["add_potato"]),
            (
                "Mysore Masala Dosa (V)",
                13.0,
                "Red chili chutney laced crepe, turmeric mash potatoes",
                [],
            ),
            ("Poondu Podi Dosa (V)", 12.0, "Roasted garlic spice dusted crepe", ["add_potato"]),
            ("Rava Dosa (V)", 12.5, "Semolina and rice batter, black pepper", ["add_potato"]),
            ("Muttai Dosa", 13.0, "Spiced egg laced crepe, curry dip, chutneys", []),
            (
                "Madurai Mutton Kari Dosai",
                15.0,
                "Fermented rice and lentil pancake, pulled lamb, egg, curry dip, chutneys",
                [],
            ),
            (
                "Rava Masala Dosa (V)",
                14.5,
                "Semolina and rice batter, Black Peppers, Turmeric mashed potatoes",
                [],
            ),
            ("Uthappam (V)", 12.0, "Roasted garlic spice dusted crepe", ["add_potato"]),
            ("Kal Dosa (V)", 12.0, "Roasted garlic spice dusted crepe", ["add_potato"]),
        ],
    ),
    (
        "நீராவி உணவு Neeraavi Unavu / Steamed Food",
        [
            ("Idiyappam (V)", 12.0, "Steamed rice string hoppers", ["aappam_side"]),
            # Aappam variant 1 of 2: full plate, choice of accompaniment.
            ("Aappam (V)", 12.0, "Lacy rice pancakes", ["aappam_side"]),
            (
                "Aappam With Asaiva Curry",
                15.0,
                "Lacy rice pancakes with a choice of chettinad chicken, mutton masala, or fish curry",
                ["asaiva_curry"],
            ),
            ("Egg Aappam 2Pcs", 14.0, "Lacy panackes topped with egg, salna on side", []),
            # Idli variant 1 of 2: 3 pcs with accompaniment choice.
            ("Idli (V)", 11.0, "Fermented rice and lentil cakes (3 pcs), sambar, chutneys", ["idli_side"]),
            ("Podi Idli (V)", 12.0, "Stir fried idli, curry leaves, chilies", []),
        ],
    ),
    (
        "தென்னிந்திய ரொட்டி Theninthiya Rotti / South Indian Breads",
        [
            ("Chapathi (2Pcs)", 13.0, "", []),
            ("Parotta (2Pcs)", 13.0, "Classic southern Indian flaky layered bread", []),
            ("Ceylon Parotta", 12.0, "", ["ceylon_filling"]),
            (
                "Kizhi Parotta",
                14.0,
                "Layered parotta steamed in banana leaf, choice of veg, chicken or mutton",
                ["protein_vcm"],
            ),
            (
                "Kothu Parotta",
                13.0,
                "Shredded Parotta with choice of Veg, Egg, Chicken or Mutton",
                ["protein_vecm"],
            ),
            (
                "Virudhunagar Parotta (2Pcs)",
                16.0,
                "Oil fried Parotta with the choice of Veg, Chicken or Mutton",
                ["protein_vcm"],
            ),
        ],
    ),
    (
        "இந்தியன் சைனீஸ் Indian Chinese",
        [
            ("Chicken Fried Rice", 17.0, "", ["spice", "add_egg"]),
            ("Egg Fried Rice", 16.0, "", ["spice"]),
            ("Veg Fried Rice", 15.0, "", ["spice", "add_egg"]),
            ("Noodles", 15.0, "Choice of vegetable, egg, or chicken", ["protein_vec", "spice"]),
            (
                "Hakka Chili (Dry / Gravy)",
                15.0,
                "Choice of paneer, chicken, or shrimp",
                ["protein_pcs", "chinese_style", "spice"],
            ),
            (
                "Manchurian",
                14.0,
                "Choice of gobhi, chicken, or shrimp",
                ["protein_gcs", "chinese_style", "spice"],
            ),
            (
                "Schezwan (Dry / Gravy)",
                15.0,
                "Choice of paneer, chicken, or shrimp",
                ["protein_pcs", "chinese_style", "spice"],
            ),
        ],
    ),
    (
        "பக்கம் Pakkam / Sides",
        [
            ("White Rice", 4.0, "", []),
            # These four collide by name with full items in the breads and
            # steamed sections. Renamed so the agent can say which is which.
            # Toast calls them "Parotta", "Chapathi", "Idiyappam (2)", "Aappam (1)".
            ("Parotta (1 Pc Side)", 4.0, "1 piece", []),
            ("Chapathi (1 Pc Side)", 3.0, "1 piece", []),
            ("Idiyappam (2 Pc Side)", 4.0, "2 pieces", []),
            # Idli variant 2 of 2: single piece side, no accompaniment.
            ("Idli - Pc", 2.5, "1 piece", []),
            # Aappam variant 2 of 2: single piece side, no accompaniment.
            ("Aappam (1 Pc Side)", 4.0, "1 piece", []),
            ("Potato Masala", 3.0, "", []),
            ("Yogurt", 3.0, "", []),
            ("Pappad", 2.0, "", []),
            ("Raita", 3.0, "", []),
            ("Sambar 8oz", 4.0, "", []),
            ("Jeera Rice", 11.0, "", []),
            ("Sambar 16oz", 8.0, "", []),
            ("Rasam 8oz", 3.0, "", []),
            ("Rasam 16", 6.0, "", []),
            ("Omelette", 6.0, "", []),
            ("Coconut Milk", 3.0, "", []),
            ("Curd Rice", 11.0, "", []),
            ("Egg", 6.0, "", []),
        ],
    ),
    (
        "இனிப்பு Inippu / Desserts",
        [
            ("Paal Kozhukattai", 8.0, "Sweet rice dumplings, jaggery, coconut", []),
            ("Warm Badam Halwa", 8.0, "Saffron, almonds, clarified butter", []),
            ("Faluda", 8.0, "Basil seeds, rice noodles, ice cream", []),
            ("Elaneer Payasam", 8.0, "Tender coconut pudding, Ice Cream", []),
            (
                "Kavuni Rice Pudding",
                8.0,
                "Traditional black rice pudding, cardamom, vanilla bean ice cream",
                [],
            ),
            ("Jigarthanda", 8.0, "", []),
            ("Blueberry Faluda", 8.0, "", []),
            ("Sweet Beeda / Paan", 2.0, "", []),
        ],
    ),
    (
        "குளிர்பானம் Kulirpaanam / Cold Drinks",
        [
            ("Watermelon Juice", 7.0, "", []),
            ("Tender Coconut Water", 7.0, "", []),
            ("Cucumber Mint Cooler", 7.0, "", []),
            ("Mango Kulukki Sarbath", 7.0, "", []),
            ("Nannari Sharbat", 7.0, "", []),
            ("Pomegranate Lemonade", 7.0, "", []),
            ("Neer Mor Soda", 7.0, "", []),
            ("Fresh Lime Soda", 6.0, "", []),
            ("Buttermilk", 6.0, "", []),
            (
                "Coca-Cola",
                3.0,
                "Coca-Cola Original Taste — the crisp, refreshing taste you know and love",
                [],
            ),
            ("Diet Coke", 3.0, "Take a Diet Coke break with this refreshing, no-calorie soft drink", []),
            ("Sprite", 3.0, "Classic, cool, crisp lemon-lime flavored taste that's caffeine free", []),
            ("Moru Thatha", 7.0, "", []),
            ("Lassi", 7.0, "", []),
        ],
    ),
    (
        "சூடான பானங்கள் Suudana Panangal / Hot Beverages",
        [
            ("Kumbakonam Degree Kaapi", 4.0, "Traditional filter coffee", []),
            ("Sukku Malli Kaappi", 3.0, "Palm sugar, dry ginger", []),
            ("Masala Tea", 4.0, "", []),
        ],
    ),
]


def spoken_name_for(name: str):
    """Explicit override, else strip a bare '(V)' marker, else no spoken form."""
    if name in SPOKEN_NAMES:
        return SPOKEN_NAMES[name]
    if "(V)" in name:
        return re.sub(r"\s*\(V\)", "", name).lower()
    return None


def build_group(section: str, item_name: str, key: str) -> dict:
    name, options, required, min_sel, max_sel = MODS[key]
    base = f"{section}|{item_name}|{name}"
    return {
        "name": name,
        "guid": g("group", base),
        "isRequired": required,
        "minSelections": min_sel,
        "maxSelections": max_sel,
        "options": [
            {
                "name": o if isinstance(o, str) else o[0],
                "guid": g("option", base, o if isinstance(o, str) else o[0]),
                "price": 0.0 if isinstance(o, str) else o[1],
            }
            for o in options
        ],
    }


def build_item(section: str, name: str, price: float, desc: str, mod_keys: list) -> dict:
    item = {
        "name": name,
        "guid": g("item", section, name),
        "price": price,
        "description": desc,
        "modifierGroups": [build_group(section, name, k) for k in mod_keys],
    }
    spoken = spoken_name_for(name)
    if spoken:
        item["spoken_name"] = spoken
    return item


def build() -> dict:
    return {
        "menuGroups": [
            {
                "name": section,
                "guid": g("section", section),
                "items": [build_item(section, *row) for row in items],
            }
            for section, items in MENU
        ]
    }


if __name__ == "__main__":
    menu = build()
    out = Path(__file__).parent / "menu.json"
    out.write_text(json.dumps(menu, indent=2, ensure_ascii=False))
    n_items = sum(len(grp["items"]) for grp in menu["menuGroups"])
    n_spice = sum(
        1
        for grp in menu["menuGroups"]
        for i in grp["items"]
        for m in i["modifierGroups"]
        if m["name"] == "Spice Level"
    )
    print(f"wrote {out}: {len(menu['menuGroups'])} groups, {n_items} items, {n_spice} with spice")
