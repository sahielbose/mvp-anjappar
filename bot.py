#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Pipecat Twilio Phone Example.

The example runs a simple voice AI bot that you can connect to using a
phone via Twilio.

Required AI services:
- Deepgram (Speech-to-Text)
- OpenAI (LLM)
- ElevenLabs (Text-to-Speech)

The example connects between client and server using a Twilio websocket
connection.

Run the bot using::

    # Telephony (Twilio, 8kHz mu-law):
    uv run bot.py -t twilio -x your_ngrok.ngrok.io

    # Local browser-mic testing over WebRTC (16kHz, no Twilio number needed):
    uv run bot.py -t webrtc

Both modes share the same STT, LLM, TTS and ordering tools. They differ only in
transport and sample rate; see SAMPLE_RATES below.
"""

import os
from pathlib import Path

from deepgram import LiveOptions
from dotenv import load_dotenv
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.frameworks.rtvi import RTVIConfig, RTVIObserver, RTVIProcessor
from pipecat.runner.types import RunnerArguments, SmallWebRTCRunnerArguments
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport

load_dotenv(override=True)

from ordering import keyterms as menu_keyterms  # noqa: E402  (needs load_dotenv first)
from ordering import tools as ordering_tools  # noqa: E402  (needs load_dotenv first)

PROMPT_PATH = Path(__file__).parent / "prompts" / "system.txt"

# Twilio carries 8kHz mu-law. SmallWebRTC negotiates Opus (48kHz) and pipecat's
# transport resamples input down to 16kHz, so local mode runs the whole pipeline
# at 16kHz. Local audio will sound noticeably better than the phone path; judge
# telephony quality on the Twilio path, not this one.
TELEPHONY_SAMPLE_RATE = 8000
LOCAL_SAMPLE_RATE = 16000

# The seven ordering tools. Handlers live in ordering/tools.py; the cart is
# server-side state there, never in the model's context.
ORDERING_FUNCTIONS = {
    "search_menu": ordering_tools.search_menu,
    "add_item": ordering_tools.add_item,
    "set_modifier": ordering_tools.set_modifier,
    "remove_item": ordering_tools.remove_item,
    "set_quantity": ordering_tools.set_quantity,
    "get_cart": ordering_tools.get_cart,
    "submit_order": ordering_tools.submit_order,
}


def _make_tool_handler(handler):
    """Wrap one ordering tool as a pipecat function handler.

    Must take exactly one parameter: pipecat treats any handler with more than
    one as the deprecated 6-positional-arg form and calls it that way, which
    breaks every tool call at runtime.
    """

    async def run_tool(params):
        # Handlers are sync, fast, and never raise: refusals come back as
        # structured dicts the model can read out loud.
        await params.result_callback(handler(**params.arguments))

    return run_tool


def register_ordering_tools(llm):
    """Route the LLM's function calls to the handlers in ordering/tools.py."""
    for name, handler in ORDERING_FUNCTIONS.items():
        llm.register_function(name, _make_tool_handler(handler))


def build_stt():
    """Deepgram, biased toward this restaurant's menu.

    This is the whole reason the bot can hear "Seeraga Samba goat biryani" over
    a phone line. Without the keyterms it is a general English model being asked
    to guess at Tamil transliterations, which is the failure the previous vendor
    shipped. nova-3 is required: keyterm prompting exists only on nova-3 and
    Flux, and it happens to be pipecat's default model already.
    """
    terms = menu_keyterms.budgeted()
    cost = sum(menu_keyterms.estimate_tokens(t) for t in terms)
    logger.info(f"Deepgram keyterms: {len(terms)} terms, ~{cost} tokens (cap 500)")

    return DeepgramSTTService(
        api_key=os.getenv("DEEPGRAM_API_KEY"),
        live_options=LiveOptions(model="nova-3-general", keyterm=terms),
    )


async def run_bot(transport: BaseTransport, sample_rate: int):
    logger.info(f"Starting bot at {sample_rate} Hz")

    # One cart per call.
    ordering_tools.reset()

    stt = build_stt()

    # The Twilio serializer µ-law encodes every outgoing frame itself, so the
    # pipeline must carry PCM. sample_rate makes ElevenLabs emit pcm at the
    # pipeline rate; requesting ulaw_8000 here would double-encode.
    tts = ElevenLabsTTSService(
        api_key=os.getenv("ELEVENLABS_API_KEY"),
        voice_id=os.getenv("ELEVENLABS_VOICE_ID"),
        model="eleven_flash_v2_5",
        sample_rate=sample_rate,
    )

    llm = OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"))
    register_ordering_tools(llm)

    messages = [{"role": "system", "content": PROMPT_PATH.read_text().strip()}]

    context = OpenAILLMContext(messages, tools=ordering_tools.TOOL_SCHEMAS)
    context_aggregator = llm.create_context_aggregator(context)

    rtvi = RTVIProcessor(config=RTVIConfig(config=[]))

    pipeline = Pipeline(
        [
            transport.input(),  # Transport user input
            rtvi,  # RTVI processor
            stt,
            context_aggregator.user(),  # User responses
            llm,  # LLM
            tts,  # TTS
            transport.output(),  # Transport bot output
            context_aggregator.assistant(),  # Assistant spoken responses
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=sample_rate,
            audio_out_sample_rate=sample_rate,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[RTVIObserver(rtvi)],
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"Client connected")
        # Kick off the conversation.
        messages.append(
            {"role": "system", "content": "Greet the caller and ask what they would like to order."}
        )
        await task.queue_frame(LLMRunFrame())

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"Client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)

    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Main bot entry point for the bot starter."""

    # Local browser-mic mode: uv run bot.py -t webrtc
    if isinstance(runner_args, SmallWebRTCRunnerArguments):
        # Imported here so the telephony path never needs aiortc installed.
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

        logger.info("Transport: SmallWebRTC (local)")
        transport = SmallWebRTCTransport(
            webrtc_connection=runner_args.webrtc_connection,
            params=TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                vad_analyzer=SileroVADAnalyzer(),
            ),
        )
        await run_bot(transport, LOCAL_SAMPLE_RATE)
        return

    transport_type, call_data = await parse_telephony_websocket(runner_args.websocket)
    logger.info(f"Auto-detected transport: {transport_type}")

    serializer = TwilioFrameSerializer(
        stream_sid=call_data["stream_id"],
        call_sid=call_data["call_id"],
        account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
        auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
    )

    transport = FastAPIWebsocketTransport(
        websocket=runner_args.websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(),
            serializer=serializer,
        ),
    )

    await run_bot(transport, TELEPHONY_SAMPLE_RATE)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
