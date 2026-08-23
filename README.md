# Anjappar Phone Ordering Bot

A voice AI agent that takes takeout orders over the phone for
[Anjappar Chettinad Cuisine](https://www.anjappardublin.com/) in Dublin, CA.

Call it, order food, it reads the order back and submits it.

**Call the demo:** `+1 XXX-XXX-XXXX`

Deepgram (speech to text) → GPT-4.1 (conversation) → ElevenLabs (voice), over a
Twilio phone number, wired together with [Pipecat](https://github.com/pipecat-ai/pipecat).

---

## What makes it more than a chatbot with a phone number

The cart lives on the server, not in the model's context. The LLM gets seven
tools and cannot invent its way around them:

| Tool | Does |
|---|---|
| `search_menu` | Fuzzy match what the caller said against 136 real menu items |
| `add_item` | Add a line, returns a `line_id` |
| `set_modifier` | Record a required choice (spice level, protein, filling) |
| `remove_item` | "Actually, drop the pappad" |
| `set_quantity` | "Make that three" |
| `get_cart` | Lines, subtotal, and what still needs asking |
| `submit_order` | Send it, if and only if the order is complete |

`submit_order` **refuses** if the cart is empty, if any required choice is
unfilled, or if the agent hasn't read the order back and got a yes. Refusals come
back as structured errors the agent can say out loud, never exceptions.

Two details that matter on a real phone line:

- **Nothing numeric is spoken raw.** Every price, quantity and awkward name ships
  with a spoken form. `17.00` → "seventeen dollars". `Gobhi 65` → "gobi sixty
  five". The TTS model doesn't normalize numbers, so the code does it.
- **Ambiguity is asked about, not guessed.** "aappam" matches both a $12 plate
  and a $4 side, so search returns both flagged `ambiguous` and the agent asks.

---

## Run it

You need [uv](https://docs.astral.sh/uv/getting-started/installation/),
[ngrok](https://ngrok.com/docs/getting-started/), and API keys for OpenAI,
Deepgram and ElevenLabs. For the phone part you also need a
[Twilio number](https://help.twilio.com/articles/223135247).

### 1. Install

```bash
git clone https://github.com/sahielbose/mvp-anjappar.git
cd mvp-anjappar
uv sync
```

### 2. Add your keys

```bash
cp env.example .env
```

Fill in `.env`:

```
OPENAI_API_KEY=
DEEPGRAM_API_KEY=
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=

# Optional: lets the bot hang up on its own
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
```

`ELEVENLABS_VOICE_ID` is the ~20-character ID from the voice's page in
ElevenLabs, not the voice's name.

### 3. Try it in your browser first

No phone number needed. This is the fastest way to hear it:

```bash
uv run bot.py -t webrtc
```

Open http://localhost:7860 and talk. First launch takes ~15s while it downloads
the voice activity detection model.

### 4. Put it on a phone number

**Terminal 1** — open a tunnel to port 7860:

```bash
ngrok http 7860
```

Copy the hostname it prints (like `abc123.ngrok-free.app`).

**Terminal 2** — start the bot with that hostname:

```bash
uv run bot.py -t twilio -x abc123.ngrok-free.app
```

Hostname only. No `https://`, no trailing slash.

**Twilio console** — open your number, and under Voice Configuration set
"A call comes in" to:

- Webhook
- `https://abc123.ngrok-free.app/` (the root path)
- **HTTP POST**

Save, then call your number.

> Free ngrok gives you a new hostname every restart, and it appears in two
> places: the `-x` flag and the Twilio webhook. Update both.

---

## Poke at it without spending money

Walk a full four-item order end to end and print every tool call. No API keys, no
audio, no network:

```bash
uv run python -m ordering.scripts.dry_run
```

Reads top to bottom like a call transcript. Good for spotting awkward phrasing
before you hear it over a phone line.

Run the tests (73 of them, all offline):

```bash
uv run pytest -q
```

---

## Layout

```
bot.py                     pipeline: transport → STT → LLM → TTS
prompts/system.txt         how the agent is told to behave
ordering/
  menu.json                136 items, 14 sections, Toast Menus V3 shape
  build_menu.py            regenerates menu.json (GUIDs are stable)
  tools.py                 the seven tools + their OpenAI schemas
  cart.py                  server-side cart, the source of truth
  toast_client.py          stub POS client, swappable for the real one
  speech.py                numbers → words
  keyterms.py              Deepgram keyterm hints for Tamil dish names
  NOTES.md                 every assumption made about the menu
  scripts/dry_run.py       the transcript walkthrough
```

### Editing the menu

Edit the table in `ordering/build_menu.py`, then:

```bash
uv run python ordering/build_menu.py
```

GUIDs are derived deterministically, so regenerating never invalidates an ID the
agent already handed out. A test fails if `menu.json` drifts from the generator.

Prices and item names came off Anjappar Dublin's live Toast ordering page.
Modifier groups (spice levels, protein choices) are **inferred**, since Toast
doesn't expose them without clicking into each item. `ordering/NOTES.md` lists
every one of those assumptions.

---

## Known gaps

- The Twilio leg hasn't been exercised end to end yet. Browser mode and the
  tool layer are tested; the 8kHz phone path is the untested piece.
- `keyterms.py` is built but not yet wired into the Deepgram service.
- Local browser mode runs at 16kHz and sounds better than the phone path, which
  is 8kHz µ-law. Judge call quality on a real call.

Not affiliated with Anjappar. Built as a demo.
