# Demo run sheet — Anjappar Dublin

Everything works except writing the order into Toast. That one gap is a Toast
access problem, not a build problem, and it's the thing you're asking him to
help unblock.

---

## Before you leave

**1. Fill in `.env`.** Four required keys:

```
OPENAI_API_KEY=          # platform.openai.com — needs billing credit
DEEPGRAM_API_KEY=        # console.deepgram.com — must support nova-3
ELEVENLABS_API_KEY=      # elevenlabs.io
ELEVENLABS_VOICE_ID=     # the ~20-char ID from the voice's page, NOT its name
```

Two more only for the phone path (without them the bot still answers, it just
can't hang up on its own):

```
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
```

**2. Pick the voice properly.** Audition several Indian English voices in
ElevenLabs before you go. The manager named the generic American voice as a
reason the last vendor failed — this is the first thing he'll judge, in the
first two seconds, before he's heard a single dish name.

**3. Do a full run yourself.** Not a smoke test — actually order four items,
change your mind halfway, and let it read the order back:

```bash
cd ~/mvp-anjappar && uv run bot.py -t webrtc
```

Then open http://localhost:7860.

**4. Confirm nothing is 86'd:**

```bash
cd ~/mvp-anjappar && uv run python -m ordering.scripts.eightysix
```

Should print `everything is available`.

**5. Check the tests are green** — 252 of them, ~4 seconds:

```bash
cd ~/mvp-anjappar && uv run pytest -q
```

---

## Running it there

**Browser (safest — no carrier, no tunnel, no ngrok URL to expire):**

```bash
cd ~/mvp-anjappar && uv run bot.py -t webrtc
```

**Over a real phone call**, two terminals plus the Twilio console:

```bash
ngrok http 7860
```

```bash
cd ~/mvp-anjappar && uv run bot.py -t twilio -x YOUR-HOST.ngrok-free.app
```

Then point the Twilio number's "A call comes in" webhook at
`https://YOUR-HOST.ngrok-free.app/` over **HTTP POST**.

Free ngrok issues a new hostname on every restart and it appears in two places —
the `-x` flag and the Twilio webhook. If the call connects and then goes silent,
that mismatch is the first thing to check.

> Do the browser run first even if you plan to demo on the phone. If ngrok or
> the carrier misbehaves in his restaurant you still have something to show.

---

## What to actually show him

**Let him drive.** The strongest thing in the room is him picking dishes off his
own menu and hearing them come back right. Hand it over early.

1. **Let him order in Tamil-accented English.** This is the whole pitch. It has
   65 menu keyterms boosted into the ASR, all 136 items are findable, and it
   handles the transliterations — "seruga samba goat biryani", "naatu kozhi
   rasam", "chapati", "prawn thoku" all resolve.

2. **Let him ask for something he doesn't sell.** Naan, samosa, tandoori
   chicken, beef curry. It says it doesn't have them instead of offering the
   nearest-sounding dish. Worth pointing out explicitly — quietly substituting is
   exactly how a phone agent loses a customer, and it's what the naive version
   of this does.

3. **Ask for something vegetarian.** "Paneer tikka", "vegetarian biryani". It
   will never offer a meat dish against a veg request.

4. **86 something in front of him.** This lands, because it's the objection he's
   about to raise anyway:

   ```bash
   uv run python -m ordering.scripts.eightysix out seeraga samba goat biryani
   ```

   Next call, the agent says they're out of it and offers something else. Then:

   ```bash
   uv run python -m ordering.scripts.eightysix back seeraga samba goat biryani
   ```

   Say plainly that this becomes automatic once Toast access lands — it's the
   `stock:read` endpoint, and that one is on the **read-only** tier we can buy
   self-serve, so it doesn't wait on the order-write approval.

5. **Change your mind mid-order.** "Actually drop the pappad, make the parottas
   three." Then let it read the whole order back with the subtotal. It will not
   submit until you confirm.

6. **It never takes a card.** Pay at pickup, by design. No card handling, no PCI
   scope, nothing to get wrong.

---

## Say this before he finds it

**Orders don't reach Toast yet.** They're written locally, and the agent tells
the caller the order is in. Tonight is the voice layer.

Frame it accurately, because it's genuinely his to unblock:

> Toast gates order-writing. The self-serve tier they sell is read-only — a POST
> returns a 403. The path for a single restaurant isn't a partner application,
> it's a *custom integration*, and Toast's own documentation says the restaurant
> has to start it: you contact your Toast account rep and request API access
> naming us as your developer. I'll draft the email. I can't give you a date
> because Toast doesn't publish one — but nothing else waits on it.

If he asks why he should bother when other vendors already have it: Loman AI
became a certified Toast partner in July. The Toast connection isn't the hard
part and it isn't what he's paying for. Getting his menu understood over a phone
line is, and that's the part nobody else has an incentive to solve for one
Chettinad restaurant in Dublin.

---

## If something breaks

| Symptom | Cause |
|---|---|
| Call connects, then silence | ngrok hostname doesn't match the `-x` flag or the Twilio webhook |
| Bot won't start | A blank key in `.env` |
| STT dies mid-call | Deepgram key not on a nova-3 plan, or a keyterm budget change pushed past 500 tokens |
| Wrong/robotic accent | `ELEVENLABS_VOICE_ID` is the voice *name* instead of the ID |
| It won't sell something | Check `uv run python -m ordering.scripts.eightysix` |

**Fallback if the audio path fails entirely.** This needs no keys and no network,
and still shows the menu intelligence:

```bash
cd ~/mvp-anjappar && uv run python -m ordering.scripts.dry_run
```

---

## Known limits, stated honestly

- **No Toast write.** The blocker.
- **Modifier groups are inferred, not observed.** Toast's listing page doesn't
  expose them without clicking into each item, so spice levels and accompaniments
  are Sahiel's best guess. Prices are real; all 136 came off the live page.
  Worth asking the manager to correct a few — it's a good conversation and it
  makes him a participant.
- **Search thresholds are tuned against invented queries**, not real call
  transcripts. The first week of real calls should retune them.
- **No customer name or phone is captured yet** — `submit_order` sends an empty
  customer object. Fine for a demo, needed before real pickup orders.
