# PRD: Anjappar Phone Ordering Agent

A voice AI agent that answers a real phone number, takes a takeout order in
natural conversation, reads it back, and submits it. Restaurant is Anjappar
Chettinad Cuisine, Dublin CA.

Reference implementation: `github.com/sahielbose/mvp-anjappar`. It works. This
PRD describes rebuilding it **without OpenAI or Deepgram API keys**, and
**self-hosted** rather than tunnelled from a laptop.

---

## 1. Read this first: the subscription question

The ask was "don't use OpenAI API keys, use our own subscription."

**A ChatGPT Plus / Team subscription does not include API access.** Neither does
Claude Pro. They are separate products with separate billing — a consumer
subscription has no API key, and driving the consumer web app programmatically
to act as an API backend violates OpenAI's terms and will get the account
banned. Same for Claude Pro. There is no supported path from a chat
subscription to an API endpoint.

So "use our subscription instead of an API key" is not buildable as literally
stated. What *is* buildable, and cheaper than OpenAI anyway, is section 3.
Pick one of those and the requirement is satisfied in spirit: **no OpenAI
spend, no Deepgram spend.**

---

## 2. What is supplied vs what must be solved

| Service | Status |
|---|---|
| Twilio (number, SID, auth token) | **Supplied** |
| ElevenLabs (API key, voice ID) | **Supplied** |
| LLM | **Must be solved without OpenAI** — see §3.1 |
| Speech to text | **Must be solved without Deepgram** — see §3.2 |
| Hosting | **Must be solved** — self-hosted, always on — see §6 |

Nothing else needs a paid account.

---

## 3. Service substitutions

The reference implementation uses Pipecat, which abstracts each service behind
a swappable class. Substituting is a constructor change, not a rewrite.

### 3.1 LLM — replacing OpenAI

Ranked. Pick one.

**Option A — Local model via Ollama (zero marginal cost, fully self-hosted)**
- `pipecat.services.ollama.llm.OLLamaLLMService`
- Already a subclass of the OpenAI service pointed at `http://localhost:11434/v1`,
  so tool calling works unchanged.
- Model must be genuinely good at function calling. Qwen 2.5 (7B+) or Llama 3.3
  are the realistic floor. Smaller models hallucinate tool arguments and will
  invent menu items, which breaks the core safety property in §5.1.
- **Tradeoff: latency.** On CPU this will be too slow for phone conversation.
  Needs a GPU host, or Apple Silicon with enough RAM. Budget under ~800ms to
  first token or the call feels broken.

**Option B — Groq (free tier, hosted, fastest option)**
- `pipecat.services.groq.llm.GroqLLMService`
- OpenAI-compatible, supports tool calling, and is dramatically faster than
  OpenAI, which matters more here than model quality.
- Requires a Groq account, but the free tier covers demo volume. **This is the
  recommended default** unless a GPU host is already available.

**Option C — Any OpenAI-compatible gateway**
- Pipecat ships services for OpenRouter, Together, Cerebras, Fireworks,
  DeepSeek, Mistral, SambaNova, NVIDIA NIM.
- Also `AnthropicLLMService` if OpenSwarm has Claude API credit.
- Any of these can also be reached by pointing the base OpenAI service at a
  different `base_url`, since it forwards to the OpenAI SDK.

**Hard requirement whichever is chosen:** the model must support **parallel-safe
structured tool calling** with 8 registered tools. If tool calling is unreliable,
nothing else in this document works.

### 3.2 Speech to text — replacing Deepgram

**Option A — Local Whisper (zero cost, fully self-hosted)**
- `pipecat.services.whisper.stt.WhisperSTTService`, or
  `WhisperSTTServiceMLX` on Apple Silicon.
- **Important: it is a `SegmentedSTTService`, not streaming.** It transcribes
  after voice-activity detection closes a segment, rather than continuously.
  Expect noticeably worse turn-taking than Deepgram — the agent will feel like
  it waits before responding. Test this before committing.
- Telephony audio is 8kHz µ-law, which is the worst case for Whisper accuracy.
  Use at minimum `distil-medium.en`; smaller models will mangle Tamil dish names.

**Option B — Groq Whisper (free tier, hosted, recommended)**
- `pipecat.services.groq.stt.GroqSTTService`
- Whisper-large quality at very low latency. Best accuracy-per-effort here.

**Known loss either way:** Deepgram nova-3 supports *keyterm prompting*, which
biases recognition toward supplied vocabulary. The reference repo generates 100
Tamil dish-name keyterms in `ordering/keyterms.py` for exactly this. **Whisper
has no equivalent that Pipecat currently exposes.** Expect worse recognition of
"Kizhi Parotta", "Poondu Kulumbu", "Elaneer Payasam".

Mitigation: the fuzzy matcher in §5.2 is what actually saves this. It already
handles transliteration variants, so a mangled transcript still resolves. Do not
skip it — without keyterm biasing it is doing more work, not less.

### 3.3 TTS — unchanged

ElevenLabs, model `eleven_flash_v2_5`, voice supplied. See §4 for the sample
rate trap.

---

## 4. Architecture

```
Caller → Twilio number → webhook POST / → TwiML → wss://host/ws
       → Pipecat pipeline: transport → STT → LLM (8 tools) → TTS → transport
       → ordering layer (server-side cart) → POS client
```

**Sample rates.** Twilio carries 8kHz µ-law. Pipecat's `TwilioFrameSerializer`
µ-law-encodes every outgoing frame itself, so the pipeline must carry **PCM**.
Configure TTS to emit PCM at 8000 Hz. Requesting `ulaw_8000` from ElevenLabs
double-encodes and produces garbage audio. This is a real trap that costs an
afternoon.

If a browser test mode is included, it runs at 16kHz and will sound better than
the phone path. Judge quality on a real call only.

---

## 5. The ordering layer — this is the actual product

A chatbot with a phone number is not the deliverable. These properties are.

### 5.1 The model must not be able to invent the menu

- The cart is **server-side state**, never in the model's context.
- The model gets tools and cannot reach around them.
- The agent may never name a dish, price, or ingredient that did not come back
  from a `search_menu` call in that conversation.

### 5.2 Eight tools

| Tool | Behaviour |
|---|---|
| `search_menu(query)` | Fuzzy match spoken text to items. Returns candidates + required choices. |
| `add_item(item_guid, qty)` | Adds a line, returns a short `line_id` the agent can say out loud. |
| `set_modifier(line_id, group_guid, option_guid)` | Records a required choice. |
| `set_customer_name(name)` | First name. Trimmed, capped, stored raw, overwrites on repeat. |
| `remove_item(line_id)` | "Actually drop the pappad." |
| `set_quantity(line_id, qty)` | "Make that three." |
| `get_cart()` | Lines, subtotal, and what still needs asking. |
| `submit_order(readback_confirmed)` | Sends it, only if complete. |

### 5.3 `submit_order` must refuse, in this order

Structured errors the agent can speak. **Never exceptions.**

1. `EMPTY_CART`
2. `MISSING_MODIFIERS` — any required choice unfilled
3. `MISSING_CUSTOMER_NAME`
4. `READBACK_REQUIRED` — the full order was not read back and confirmed

### 5.4 Fuzzy matching

- Fuzzy string match (rapidfuzz), **no embeddings**.
- A transliteration variant map is mandatory: chettinad/chetinad/chettinaad,
  parotta/paratha/porotta, gobhi/gobi, kuzhambu/kulambu/kolumbu,
  dosa/dosai/thosai, aappam/appam, kothu/kotu, biryani/biriyani, idli/idly.
- Cap candidates at 5.
- **Flag ambiguity rather than guessing.** If the top two scores are close,
  return both marked ambiguous and make the agent ask. "aappam" is a $12 plate
  and a $4 side. "parotta" is a 2-piece bread and a 1-piece side.
- Boost by menu section when the query names one ("a dosa" → the dosa section).

### 5.5 Menu data

- Toast Menus V3 shape: `menuGroups[] → items[] → modifierGroups[] → options[]`.
- 14 sections, ~136 items, real prices.
- **GUIDs must be deterministic** (uuid5 from a stable key). Regenerating the
  menu must not invalidate a GUID the agent already handed out mid-call.
- Required modifier groups only where the menu implies a real choice. Do not put
  a spice-level question on two-thirds of the menu — the agent will interrogate
  callers about dishes that have one fixed preparation. Curries and Indian
  Chinese only, roughly 25 items.

### 5.6 Nothing numeric is spoken raw

ElevenLabs Flash v2.5 does not normalize numbers. Do it in code.

Every price, quantity, name, and the pickup code ships with a spoken form:
- `17.00` → "seventeen dollars"
- `12.50` → "twelve dollars and fifty cents"
- `Gobhi 65` → "gobi sixty five"
- qty `3` → "three"
- code `4729` → "four seven two nine"

**Test: no field the agent reads aloud may contain a digit.**

### 5.7 Pickup code

- 4 characters, alphabet `23456789` — no 0/O, no 1/I/l, all unambiguous aloud.
- Deterministic from the idempotency key, so a retry returns the same code.
- Its own field, separate from the order GUID and the customer name, so it is
  trivial to delete when the real POS issues its own numbers.
- Note: 4096 combinations is **not unique**. Two same-day orders can collide.
  Disambiguate at the counter by name, or add a fifth character.

### 5.8 POS client

Two methods, so the real client drops in later without touching anything else:

```python
def get_menu(self) -> dict
def create_order(self, cart, customer, idempotency_key) -> dict
```

Stub writes to local JSON. **Same idempotency key must return the existing
order, not write a duplicate.**

---

## 6. Hosting

Currently the bot runs on a laptop behind ngrok. That is the thing to replace.

**Requirements:**
- Public **HTTPS + WSS** endpoint with a stable hostname. Twilio's webhook and
  the media stream both point at it, and a rotating ngrok URL breaks both.
- Long-lived websockets, bidirectional audio, always on.
- One process must hold a call for its full duration.

**Do not deploy to Vercel or any serverless/function platform.** This needs
persistent websocket connections and long-running processes. Functions time out
mid-call.

**Recommended: Fly.io.** Persistent machines, websockets work, TLS and a stable
hostname included, and OpenSwarm already deploys there. The repo has a
Dockerfile. Railway, Render, or any VPS with a reverse proxy also work.

If a local model is chosen (§3.1 Option A), the host needs a GPU, which changes
the platform calculus considerably. This is the strongest argument for Groq.

**Twilio config (manual, once):** on the number's Voice Configuration, set
"A call comes in" → Webhook → `https://<host>/` → **HTTP POST**.

---

## 7. Traps found the hard way

Every one of these was hit building the reference implementation.

1. **The cart must be per-connection.** A module-level cart means two
   simultaneous callers share one order, and a second caller connecting wipes
   the first caller's in-progress cart. Instantiate per websocket connection and
   bind tool handlers to that instance. Add a test for it.

2. **Remove the RTVI processor from the telephony path.** The Pipecat quickstart
   includes `RTVIProcessor`. It emits a `bot-ready` transport message that the
   Twilio serializer forwards raw, which is not a valid Media Streams message —
   Twilio logs **error 31951** on every single call. RTVI is for browser clients
   only. Keep it for a browser test mode if there is one; never on Twilio.

3. **Handler signatures.** Pipecat decides a function handler uses the deprecated
   6-positional-arg calling convention purely by counting parameters
   (`len(signature.parameters) > 1`). A late-binding default argument
   (`async def h(params, _fn=fn)`) silently trips this and **every tool call
   breaks at runtime.** Use a closure factory so handlers take exactly one param.

4. **Log every run to a file.** Twilio reports a crashed call and a normal
   hangup identically, both as `completed`. Without server-side logs a mid-call
   crash is undiagnosable. The reference implementation has an unresolved bug
   where calls cut off mid-conversation at inconsistent lengths (51s, 128s) and
   this is exactly why it is unresolved.

5. **Quantity times a name containing a count.** "3 × Parotta (2Pcs)" read back
   naively becomes "three parotta two pieces", which a caller hears as 3 or 2.
   Say "three orders of parotta, two pieces."

6. **Ask for the name at the END of the call**, just before readback. Never at
   the start. Callers hang up on agents that take their details before doing
   anything useful. Read the name back so a mangled transcript can be corrected.

---

## 8. Acceptance criteria

- A real call to the Twilio number completes a multi-item order end to end,
  including at least one required modifier and one mid-order correction.
- `submit_order` provably refuses in all four conditions in §5.3.
- No OpenAI or Deepgram spend.
- Hosted at a stable public hostname, surviving laptop shutdown.
- Full offline test suite. The reference implementation has 88 tests, all
  runnable without network, audio, or API keys — match that.
- A readable transcript dry-run script that walks a full order printing every
  tool call, for reviewing conversational flow without making a call.
- Zero Twilio error alerts on a completed call.

---

## 9. Out of scope

- Delivery. Pickup only, twenty minute quote.
- Payment over the phone. Never take a card number.
- Writing into the real Toast POS. Stub client with matching signatures only.
- Tax and fees. The subtotal is not a total, and nothing quotes a final price.
- Last name, phone number, email. First name only, one question.
