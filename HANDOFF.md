# Handoff — demo-hardening branch

**Branch:** `demo-hardening` · **Not for merge as-is.** Read, argue, cherry-pick,
build on top. Nothing here is precious except the two bug fixes.

---

## What this is and why

Shanay is demoing this to the Anjappar Dublin manager, and the thing that pushed
this branch into existence is that the manager is going to hold the phone and say
whatever he likes off his own menu — not the three dishes in the spec. So the bar
moved from "handles the showcase dishes" to "handles anything on the menu, said
the way a real person says it, and admits it when the answer is no." Working from
that bar, the first and most important find was that **the keyterms were never
reaching Deepgram**: `keyterms.py` built the list beautifully and nothing ever
imported it, so `bot.py` was constructing `DeepgramSTTService(api_key=...)` with
no `live_options` and the entire ASR differentiator — the actual reason this beats
a generic voice vendor, the reason Hosty failed — was dead code that looked wired.
That's fixed in `bot.py:build_stt()`, and while fixing it I hit the thing your
round-1 note already suspected: Deepgram hard-caps keyterms at **500 tokens across
the whole request** and returns an error rather than degrading, which drops the
call mid-conversation; `TOP_N = 100` measured at ~472 estimated tokens, inside the
cap but with no margin and on a rough estimate, so `keyterms.budgeted()` now
enforces a 380-token budget that lands at 65 terms and keeps the entire Tamil head
of the list. From there I audited all 136 items and found your exact-name recall
was already perfect — 136/136 findable, and the four side-vs-full pairs correctly
ambiguous, which is a good design and I left it alone — but a set of 106 realistic
caller phrasings only scored 95, and **every single miss was semantic rather than
orthographic**: `VARIANTS` folds misspellings of one word onto a canonical form and
by construction cannot bridge two genuinely different words, so "goat curry"
returned nothing at all even though in Indian English mutton *is* goat and that is
an entirely ordinary thing to ask at a Chettinad restaurant, and the same held for
"eggplant", "cauliflower", "garlic curry", "anchovies", "king fish", "egg dosa",
"filter coffee", "chai" and "coke". The new `ALIASES` map fixes those, and the one
design decision worth your scrutiny is that it is applied as **query expansion and
never as rewriting**, because both sides of most of these pairs are real items here
— `Seeraga Samba Goat Biryani` and `Mutton Masala` both exist — so folding
goat→mutton would have made the goat biryani unfindable; scoring takes the best
score any variant achieves, so an alias can only ever raise an item's rank and can
never displace a literal match, and that constraint is what the test
`test_an_alias_never_outranks_a_literal_match` pins down. Two behaviours then came
out of testing the failure direction rather than the success direction: **"beef
curry" was answering with Pepper Chicken Curry** at 85.5 and "naan" with Naattu
Kozhi Rasam at 77.1, which to a caller reads as the restaurant quietly substituting
on them and, in the beef case at an Indian restaurant, is worse than that — and a
score threshold provably cannot fix it, because "lamb curry" and "beef curry" score
*identically* against Pepper Chicken Curry since the word "curry" carries the whole
match, so the discriminator I used instead is that `lamb` is explained by an alias
while `beef` is explained by nothing, and a query word found in neither the menu
vocabulary nor `ALIASES` with no candidate above 90 now returns zero candidates
plus `unmatched_terms` for the agent to name back, while genuine misspellings
survive on score (`chapati` 93.3, `seruga samba goat biryani` 104.1). The second
was **"vegetarian biryani" returning "Chennai Style Plain Biryani (Non Veg)"** as
its top hit, which is a real bug in your normalization rather than a tuning issue:
`_normalize` strips parentheticals, so the `(V)` and `(Non Veg)` markers — the only
thing distinguishing two otherwise near-identical biryani names — were gone before
matching ever happened, and `_is_non_veg()` now reads the raw name instead. Finally
I closed the 86'd-items gap your round-1 notes flagged as unresolved ("the agent
will currently take an order for a biryani the kitchen cannot make"), with
`availability.py` plus a staff toggle script; the deliberate choice there is that
**the 16 items you saw OUT OF STOCK on 2026-08-22 are not baked in**, because stock
moves daily and an agent refusing to sell something the kitchen actually has is the
same failure pointing the other way — everything ships available, and this whole
module is a placeholder for Toast's `stock:read`, which matters because stock is on
the **read-only** API tier that can be bought self-serve, making it the one Toast
capability that does *not* wait on order-write approval.

---

## Two things that were plain bugs, independent of any of the above

1. **The Dockerfile only copied `bot.py`.** Not `ordering/`, not `prompts/`. That
   image builds fine and then dies on the first import. If you'd deployed to
   Pipecat Cloud this is what you'd have hit.
2. **The `(Non Veg)` marker was being stripped before matching** (described
   above). Worth a look even if you throw out the rest of the veg handling.

---

## Map of the changes

| File | What |
|---|---|
| `bot.py` | New `build_stt()`. Only real change: STT now gets `live_options` with the keyterms. |
| `ordering/keyterms.py` | `estimate_tokens()` + `budgeted()`. Original `keyterms()` untouched. |
| `ordering/tools.py` | **The one file with real edits to existing logic.** See below. |
| `ordering/availability.py` | New. 86'd list, Toast `stock:read` shaped. |
| `ordering/scripts/eightysix.py` | New. Staff toggle, uses your own fuzzy search. |
| `ordering/test_menu_coverage.py` | New. 174 tests. |
| `test_bot_wiring.py` | +6 tests asserting keyterms actually reach Deepgram. |
| `prompts/system.txt` | Rules for `unmatched_terms` and `available`. |
| `Dockerfile` | Copy `ordering/` and `prompts/`. |
| `ordering/NOTES.md` | Round 3 section, same format as yours. |
| `DEMO.md` | Run sheet for the demo. Not code. |

### The bit to actually review: `ordering/tools.py`

Everything else is additive. This one changes `search_menu`'s existing scoring
loop — it used to call `process.extract` once against the normalized query and
now calls it once per alias variant, keeping the best score per item. Same
scorer, same cutoff, same ranking and ambiguity rules.

**Two new return shapes that existing callers need to know about:**

- `search_menu` can return `candidates: []` plus `unmatched_terms` (e.g. "beef
  curry"), where it previously returned a near-miss.
- `add_item` can return `{"ok": False, "error": "UNAVAILABLE"}`. The prompt
  handles it; any future code calling `add_item` directly must too.

---

## Where I'd push back on myself

- **`_CONFIDENT_SCORE = 90.0` and `_VEG_CONFLICT_PENALTY = 35.0` are my guesses**,
  same caveat as your existing thresholds. They're tuned against invented queries,
  not real call transcripts. First week of real calls should retune all of them
  together.
- **`ALIASES` is hand-written and definitely incomplete.** I only added entries
  grounded in a word that appears in an actual item name. The manager will produce
  ten more in ten minutes — that's a good thing to do in front of him.
- **The unknown-word rule is blunt.** "aloo gobi" now returns nothing even though
  Gobhi 65 is arguably a fair offer. I chose honest-and-silent over
  helpful-and-wrong, but that's a judgement call and you may disagree.
- **`estimate_tokens()` is deliberately pessimistic** — Deepgram doesn't publish
  its tokenizer, so I assumed ~1 token per 3 chars. If you can measure the real
  count, the budget could carry more like 85–90 terms instead of 65.

## Still open

- **`transfer_to_human` does not exist.** The spec lists it and calls graceful
  handoff the thing that separates us from Hosty. It's the biggest remaining gap
  and it's tangled with telephony: the call only reached the bot because nobody
  answered the main line, so transferring back there loops.
- **Toast order-write.** `toast_client.py` is still the local-JSON stub. The
  research is in `~/Downloads/toast-integration-research.md`; short version is the
  self-serve API tier is read-only and the single-restaurant path is a *custom
  integration* that Anjappar has to request from their own Toast rep.
- **No customer name/phone captured** — `submit_order` sends `customer={}`.
- **Orders written in a container are ephemeral** (`ordering/orders/` on local
  disk). Needs to point at Supabase before anything real depends on it.
- **Modifier groups are still inferred**, exactly as your round-1 notes say.

## Verify

```bash
uv sync && uv run pytest -q      # 252 passed
uv run ruff check .              # clean
uv run python -m ordering.scripts.dry_run
```
