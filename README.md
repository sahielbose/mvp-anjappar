# Anjappar Phone Ordering Bot

An AI voice agent that takes takeout orders over the phone for Anjappar
Chettinad Cuisine in Dublin, CA.

**Call it: (925) 396-7124**

Built as an OpenSwarm application.

---

## Setup

You need [uv](https://docs.astral.sh/uv/getting-started/installation/),
[ngrok](https://ngrok.com/docs/getting-started/), and API keys for OpenAI,
Deepgram and ElevenLabs. For the phone part, a
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

### 3. Test it in your browser

No phone number needed:

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

Copy the hostname it prints, like `abc123.ngrok-free.app`.

**Terminal 2** — start the bot with that hostname:

```bash
uv run bot.py -t twilio -x abc123.ngrok-free.app
```

Hostname only. No `https://`, no trailing slash.

**Twilio console** — open your number. Under Voice Configuration, set
"A call comes in" to:

- Webhook
- `https://abc123.ngrok-free.app/` (the root path)
- **HTTP POST**

Save, then call your number.

> Free ngrok gives you a new hostname every restart, and it appears in two
> places: the `-x` flag and the Twilio webhook. Update both.

---

Not affiliated with Anjappar. Built as a demo.
