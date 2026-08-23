# NOTES — assumptions to correct against real Toast data

Everything here is something I inferred, invented, or decided. Sorted roughly by
how likely it is to be wrong.

---

# Round 3 changes

## Concurrency: the cart was global (fixed)

**It was module-level state.** `ordering/tools.py` held one `_session` for the
whole process, and `bot.py` called `reset()` on every connection. Two callers at
once would have shared a cart, and worse, the second caller connecting would
have wiped the first caller's in-progress order mid-call.

Fixed: the tools are now methods on `Session`, and `run_bot` builds one
`Session` per websocket connection. Handlers are bound to that instance at
registration time, so nothing resolves globally when a tool fires. The
module-level `_session` and the thin wrapper functions still exist, but only
tests and the dry run use them — there is a test asserting `run_bot` does not.

Never had a chance to bite in production, since the Twilio path has not taken a
real call yet.

## Customer name

`set_customer_name` is tool 8. Trimmed, capped at 40 characters, stored exactly
as given — no capitalisation fixing, no spelling correction. An agent that
"corrects" an unusual name is worse than one that repeats it back verbatim and
lets the caller fix it.

Calling it again overwrites, so a misheard name is one more tool call to fix.
Changing it **invalidates the readback**, consistent with every other cart
mutation: the caller confirmed a different order than the one you now hold.

`submit_order` refuses with `MISSING_CUSTOMER_NAME`, ordered after the modifier
check and before the readback check, so the agent collects the name and then
confirms everything at once.

## Pickup code

Four digits from the alphabet `23456789`. Excluding 0 and 1 removes the whole
0/O and 1/I/l family in one move, and digits are easier to say than a mixed
alphabet. 4096 combinations, which is fine for same-day pickup collisions but
**not unique** — two orders on one day can share a code. If that matters, the
counter should disambiguate by name, or the code should get a fifth character.

Derived by `sha256(idempotency_key)`, so it is stable for a given key
independent of the stored order file. Idempotent replay returns it unchanged
both ways: the code is deterministic *and* it is saved in the response.

It is deliberately its own field, separate from `orderGuid` and from the
customer name. Real Toast issues its own order numbers, so `pickupCode` and
`spokenPickupCode` are the two things to delete when the real client lands.

## Still open

- The subtotal is not a total: no tax, no fees. Nothing quotes a final price to
  the caller yet.
- `MAX_NAME_LEN = 40` truncates silently rather than erroring. Fine for a name;
  worth revisiting if the field is ever reused.

---

# Round 2 changes

## Spice Level: 68 items → 26

Kept **only** on Mukkiya Unavu gravy curries (19) and the whole Indian Chinese
section (7). Dropped from appetizers, soups, signature dishes, biryanis, meals,
dosas, steamed food, breads, sides, desserts and drinks.

**Main Course, kept (19):** Ennai Kathirikai, Drumstick Paya, Seasonal Veg
Kuruma, Paneer Therakkal, Poondu Kulumbu, Anjappar Chicken Masala, Pepper
Chicken Curry, Chettinad Chicken Masala, Karaikudi Pepper Crab, Mangai Meen
Kulambu, Mutton Masala, Mutton Paaya, Mutton Kudal Kootu, Butter Chicken,
Chicken Tikka Masala, Paneer Butter Masala, Kadai Paneer, Kadai Vegetable,
Egg Curry.

**Indian Chinese, kept (all 7):** Chicken Fried Rice, Egg Fried Rice, Veg Fried
Rice, Noodles, Hakka Chili, Manchurian, Schezwan.

**Main Course, dropped (4)** — the rule I used is *gravy gets a spice choice, dry
preparations don't*, since a dry roast's heat is fixed by its masala rather than
adjusted at the pass:

- **Nattukozhi Ghee Roast** — "dry chettinad masala"
- **Aatu Kari Sukka** — *sukka* is a dry roast
- **Mutton Liver Roast** — dry roast
- **Prawn Thokku** — "stir-fried", a thick dry masala

That lands at **26**, one above your 20-25 range. I'd rather hand you a stated
rule you can overrule than shave an arbitrary item to hit the number. If you
want it under 25, dropping **Mutton Paaya** and **Mutton Kudal Kootu** (both
bone/offal stews with a fixed preparation, and Kudal Kootu is 86'd anyway) gets
you to 24 without touching the rule.

## GUIDs are now deterministic

`build_menu.py` derives every guid from `uuid5` over a stable key
(`section | item | group | option`) under a fixed namespace, so re-running is
byte-identical. Verified by md5 across separate processes and by two tests, one
of which asserts the checked-in `menu.json` still matches what the generator
produces — that catches editing the table without regenerating.

**Changing `NS` in `build_menu.py` rotates every guid in the menu.** When real
Toast guids arrive, pin them in the table rather than deriving them.

## Renamed the four colliding sides

Toast gives these the same written name as a full-size item in another section.
Renamed so the agent can say which is which; the portion counts come from
Toast's own descriptions, so nothing is invented:

| Toast's name | Now | Collided with |
|---|---|---|
| Parotta | **Parotta (1 Pc Side)** | Parotta (2Pcs), breads |
| Chapathi | **Chapathi (1 Pc Side)** | Chapathi (2Pcs), breads |
| Idiyappam (2) | **Idiyappam (2 Pc Side)** | Idiyappam (V), steamed |
| Aappam (1) | **Aappam (1 Pc Side)** | Aappam (V), steamed |

`Idli - Pc` was already distinguishable and is unchanged. Search returns the side
*and* the full item for all four, flagged `ambiguous`, so the agent asks.

**One existing test changed** as a direct result: `test_ambiguous_aappam_returns_both_variants`
now expects `Aappam (1 Pc Side)`. The behaviour it asserts is identical.

## Search ranking

- Capped at **5** candidates.
- **`ambiguous: true`** when the top two scores are within **8 points**. Tuned so
  "chicken" (95 vs 90) is ambiguous but "chettinaad chicken" (95 vs 85.5) is not.
  It is a guess; real call transcripts should retune it.
- **Group boost: +10** when the query names a section (dosa, biryani, soup,
  dessert, juice, lassi, coffee, tea). Deliberately *not* applied to parotta,
  idli or aappam — those are exactly the words where I want candidates spread
  across sections rather than pulled into one.
- Filler words (`a`, `the`, `some`, `please`, `with`…) are stripped before matching.

The boost is load-bearing for queries like **"egg dosa"**: without it the side
item `Egg` and the egg biryanis outrank every actual dosa. There's a test for
exactly that, because the "a dosa" test alone passes with the boost disabled.

## Spoken forms

`speech.py` converts numbers to words. `get_cart()` and `search_menu()` now
return `spoken_price` beside every `price`, plus `spoken_subtotal`, and cart
lines carry `spoken_qty`. **80 of 136 items** carry a `spoken_name`.

I added **`spoken_qty`** beyond what you asked for: the dry run showed the agent
emitting a bare "3" in the readback, which Flash v2.5 won't normalize either.
A test asserts no `spoken_*` field anywhere contains a digit.

`spoken_name` covers every item with a digit, every item with a slash (which
reads as "slash"), and the transliteration traps. Items whose only problem is a
trailing `(V)` get it stripped automatically. **All of these are my ear, not the
restaurant's** — worth a read-through, particularly "viroodhunagar",
"koozhi paniyaram", and "jigar thanda".

Note `spoken_name` is snake_case while the rest of the schema is camelCase,
because that's the field name you specified.

## What the dry run exposed

Two things worth deciding on before this goes near a phone line:

**1. Quantity times a name that contains a count.** "3 × Parotta (2Pcs)" read
back naively becomes *"three parotta, two pieces"*, which a caller will hear as
either 3 or 2. The script now says *"three orders of parotta, two pieces"*. This
is a menu-naming problem, not a code bug, and it affects every `(2Pcs)` item.

**2. "mango lassi" is a near-miss.** The menu has no mango lassi. Search returns
plain `Lassi` (100) and `Mango Kulukki Sarbath` (tied, boosted) — correctly
flagged ambiguous — but also `Mangai Meen Kulambu`, a fish curry, at rank 3,
because "mango" fuzzy-matches "mangai". Harmless while the agent asks, but it
shows fuzzy matching alone will surface a main course during a drinks question.
A section filter on drink-shaped queries would fix it if it bothers you.

---

# Round 1 notes

## Where the data came from

Item **names, prices and descriptions** were transcribed from the live Toast
ordering page on **2026-08-22**:
`https://order.toasttab.com/online/anjappar-dublin-summit-3996-summit-road`

WebFetch gets a 403 from Toast, so I read the rendered page in a browser.
`anjappardublin.com/menus/` carries no prices at all — it only confirmed the
14 section names.

**The one thing that matters most:** Toast's listing page does not show modifier
groups. You only see them by clicking into each item. So:

- **All 136 item prices are real.** I invented none.
- **Every single modifier group in `menu.json` is inferred or invented.** None of
  them were observed. This is the whole correction surface.

Only two invented **prices** exist, both on modifier options:
`Add Potato Masala = $2.00` and `Add Egg = $2.00`.

## Things in your spec that do not exist on the live menu

These are the highest-value corrections — your brief described a menu that
differs from what Toast currently serves.

**1. "Chennai Style Dum Biriyani → protein: vegetable / chicken / mutton" — no such item.**
The live menu has **12 separate biryani items** with the protein baked into the
name (Chennai Style Mutton / Chicken / Plain / Chicken 65 / Egg Biryani, plus
six Seeraga Samba variants and a veg one). There is no single biryani item with a
protein choice. I did **not** add a protein modifier group to any biryani —
adding one would have created a combinatorial mess against items that already
name their protein. Tell me if the real Toast data has a dum biriyani with a
protein group and I'll restructure.

**2. "Stuffed Ceylon Parotta" — the live item is just "Ceylon Parotta", $12.00, no description.**
I added your required Filling group (Plain Egg / Chicken / Mutton Kheema) as
specified, but nothing on the page corroborates it. Unverified.

**3. "Fried Rice → vegetable / egg / chicken" — fried rice is already split into three items.**
Live: Chicken Fried Rice $17, Egg Fried Rice $16, Veg Fried Rice $15. No protein
group added. **Noodles** ($15) *is* a single item whose description reads
"Choice of vegetable, egg, or chicken", so that one got the group.

**4. Rava Dosa "Add potato masala $2"** — not visible on the listing page. Added
as instructed, with the $2 invented. I also applied it to four other plain dosas
(Nei Roast, Poondu Podi, Uthappam, Kal Dosa) on the assumption it generalizes —
that generalization is mine, not the menu's.

## Modifier groups I invented outright

| Group | Applied to | Basis |
|---|---|---|
| **Spice Level** (Mild/Medium/Spicy, $0) | ~~68 items~~ → **26 items** in round 2 | Entirely invented. See the round 2 list above. |
| **Accompaniment** (Coconut Milk / Vegetable Stew / Salna) | Aappam (V), Idiyappam (V) | Invented to make the two Aappam variants differ, per your "different accompaniment choices" note. |
| **Accompaniment** (Sambar / Coconut Chutney / Milagai Podi) | Idli (V) | Same. The description already says "sambar, chutneys", so this may be wrong — it may be included, not chosen. |
| **Style** (Dry / Gravy) | Hakka Chili, Schezwan, **Manchurian** | Hakka Chili and Schezwan literally have "(Dry / Gravy)" in their names, so those two are near-certain. **Manchurian does not** — I added it by analogy. Likely wrong. |
| **Add Egg** ($2) | Chicken Fried Rice, Veg Fried Rice | Invented, including the price. |

### Spice Level was the biggest judgement call

**Superseded in round 2** — cut from 68 items to 26. The exact list is at the top
of this file. Original note: I had applied it to two-thirds of the menu, which
would have made the agent ask "mild, medium or spicy?" on things like Chicken
Lollipop and biryani where the kitchen likely has one fixed preparation.

## Modifier groups I'm reasonably confident about

These came from item descriptions on the live page, not invention:

- **Kizhi Parotta** → Protein (Vegetable/Chicken/Mutton) — desc: "choice of veg, chicken or mutton"
- **Kothu Parotta** → Protein (Vegetable/Egg/Chicken/Mutton) — desc: "choice of Veg, Egg, Chicken or Mutton"
- **Virudhunagar Parotta** → Protein (Vegetable/Chicken/Mutton) — desc: "the choice of Veg, Chicken or Mutton"
- **Noodles** → Protein (Vegetable/Egg/Chicken) — desc: "Choice of vegetable, egg, or chicken"
- **Hakka Chili / Schezwan** → Protein (Paneer/Chicken/Shrimp) — desc matches
- **Manchurian** → Protein (Gobhi/Chicken/Shrimp) — desc matches
- **Aappam With Asaiva Curry** → Curry (Chettinad Chicken / Mutton Masala / Fish Curry) — desc matches

Still unverified: whether any of these carry a price delta (mutton usually costs
more than veg). I set every option to **$0.00**. That is very likely wrong for the
protein groups.

## Aappam and Idli duplicates

You said each appears twice with different accompaniments. On the live menu they
appear more than twice:

- **Aappam ×4**: `Aappam (V)` $12 (Steamed Food), `Aappam With Asaiva Curry` $15,
  `Egg Aappam 2Pcs` $14, `Aappam (1)` $4 (Sides)
- **Idli ×3**: `Idli (V)` $11 (Steamed Food), `Podi Idli (V)` $12, `Idli - Pc` $2.50 (Sides)

I modeled the pair you meant as the **full portion vs. the single-piece side** —
distinct guids, distinct menu groups, and I kept Toast's exact names rather than
renaming them, since the names already differ and fidelity to real data seemed
more useful for tomorrow. `search_menu("aappam")` returns both at score 100 with
their menu group attached, so the agent asks which one. Same for `"idli"`.

If by "different accompaniment choices" you meant `Aappam (V)` vs
`Aappam With Asaiva Curry`, say so and I'll re-pair them.

## Out-of-stock items are in the menu

**16 items were marked OUT OF STOCK** on the page and I included them anyway,
because the Menus V3 shape you specified has no availability field:

- 11 of 12 biryanis (everything except Seeraga Samba Veg Biryani)
- Mutton Liver Roast, Mutton Kudal Kootu
- Jigarthanda, Blueberry Faluda, Sweet Beeda / Paan

**The agent will currently take an order for a biryani the kitchen cannot make.**
Worth adding an `available` field, or pulling live 86'd status from Toast.

## Design decisions worth a second look

~~**Only five tools, so no `remove_item` / `set_qty` tool.**~~ **Fixed in round 2:**
`remove_item(line_id)` and `set_quantity(line_id, qty)` are now tools 6 and 7,
schema'd and tested. Both return the updated cart and reset the readback flag.
`set_quantity(..., 0)` is rejected rather than treated as a removal — the agent
must call `remove_item` so the intent is explicit.

**Readback is a parameter, not a separate tool.** `submit_order(readback_confirmed=True)`,
again to stay at five functions. Any cart mutation resets the flag
(`Cart._touch`), so adding an item after readback forces a re-read. That's
deliberate, and tested.

**`"kotu parota"` ranks plain `Kothu Parotta` first, not `Crispy Crab Kothu Parotta`.**
Your spec wanted it to resolve to the crab one, but the menu has a literal
`Kothu Parotta` ($13, Breads) alongside `Crispy Crab Kothu Parotta` ($18,
Signature). The plain item is the closer string match and scores 100 vs 90. I did
not special-case it — rigging the ranking to satisfy the test would hide a real
ambiguity the agent must resolve out loud. The test asserts the crab version is
**among** the candidates and documents that the plain one ranks first. Change the
ranking only if the restaurant says callers mean the crab dish by default.

**Subtotal only.** No tax, tip, delivery fee, or service charge anywhere.

**Fuzzy search returns up to 6 candidates above score 62.** Both thresholds are
guesses tuned against the handful of queries in the tests; they need real call
transcripts to calibrate.

## Keyterms

`keyterms.py` ranks by an orthographic heuristic — tokens containing `zh`, `aa`,
`ee`, `ai`, `mbu`, `adu` etc., minus a hand-written stoplist of ~90 common English
food words. It is a heuristic, not a measurement. The right way to build this
list is from actual Deepgram transcripts of real calls, keeping the terms that
actually get mis-transcribed. Until then the top of the list looks sane
(`Seerga Samba Nattu Kozhi Biryani`, `Madurai Mutton Kari Dosai`,
`Paal Kozhukattai`, `Poondu Kulumbu`).

Note nova-3 caps keyterms per request; 100 phrases may exceed what's useful or
allowed. Verify against your Deepgram plan.

## Regenerating

`menu.json` is generated, not hand-written, so corrections are cheap:

```bash
uv run python ordering/build_menu.py
```

Edit the `MENU` table or `MODS` entries in `build_menu.py` and re-run. ~~GUIDs are
regenerated on every run (uuid4)~~ — **fixed in round 2**, guids are now uuid5 and
stable across runs, so regenerating no longer invalidates guids the agent already
handed out. `test_checked_in_menu_matches_generator` fails if you edit the table
and forget to re-run.

## Also in round 2

- `Aappam (1)` is now `Aappam (1 Pc Side)` (see the rename table above), so the
  round 1 references to `Aappam (1)` in this file are stale.
- `scripts/dry_run.py` walks a four-item order end to end and prints every tool
  call: `uv run python -m ordering.scripts.dry_run`. Not a test — it's for
  hearing the flow before the phone line does.
