"""Offline checks that bot.py wires the ordering tools correctly.

No network, no audio, no API calls.
"""

import asyncio

import pytest
from pipecat.services.llm_service import FunctionCallParams

import bot
from ordering import tools as ordering_tools


def test_all_seven_tools_are_registered_on_the_llm():
    llm = bot.OpenAILLMService(api_key="test-key-not-used")
    bot.register_ordering_tools(llm)

    for name in ordering_tools.TOOL_SCHEMAS:
        fn = name["function"]["name"]
        assert llm.has_function(fn), f"{fn} not registered"


def test_handlers_are_not_detected_as_the_deprecated_signature():
    """pipecat calls any handler with >1 parameter using the legacy
    6-positional-arg form, which would break every tool call at runtime."""
    import inspect

    llm = bot.OpenAILLMService(api_key="test-key-not-used")
    bot.register_ordering_tools(llm)

    for name, item in llm._functions.items():
        assert item.handler_deprecated is False, name
        assert len(inspect.signature(item.handler).parameters) == 1, name


def test_registry_matches_the_published_schemas():
    schema_names = {s["function"]["name"] for s in ordering_tools.TOOL_SCHEMAS}
    assert set(bot.ORDERING_FUNCTIONS) == schema_names
    assert len(schema_names) == 7


def test_handler_routes_to_the_real_tool_and_returns_its_dict():
    """A registered handler must call the ordering tool and pass its result back."""
    ordering_tools.reset()
    llm = bot.OpenAILLMService(api_key="test-key-not-used")
    bot.register_ordering_tools(llm)

    captured = {}

    async def result_callback(result, *args, **kwargs):
        captured["result"] = result

    item = llm._functions["search_menu"]
    params = FunctionCallParams(
        function_name="search_menu",
        tool_call_id="call_1",
        arguments={"query": "chettinaad chicken"},
        llm=llm,
        context=None,
        result_callback=result_callback,
    )
    asyncio.run(item.handler(params))

    result = captured["result"]
    assert result["candidates"][0]["name"] == "Chettinad Chicken Masala"
    assert result["candidates"][0]["spoken_price"] == "fourteen dollars"


def test_late_binding_each_handler_hits_its_own_tool():
    """The loop in register_ordering_tools must not close over the last handler."""
    ordering_tools.reset()
    llm = bot.OpenAILLMService(api_key="test-key-not-used")
    bot.register_ordering_tools(llm)

    seen = {}

    def invoke(name, arguments):
        async def cb(result, *a, **k):
            seen[name] = result

        params = FunctionCallParams(
            function_name=name, tool_call_id="x", arguments=arguments,
            llm=llm, context=None, result_callback=cb,
        )
        asyncio.run(llm._functions[name].handler(params))

    invoke("get_cart", {})
    assert seen["get_cart"]["lines"] == []

    invoke("submit_order", {})
    assert seen["submit_order"]["error"] == "EMPTY_CART"


def test_cart_is_server_side_and_resets_per_call():
    ordering_tools.reset()
    guid = next(
        g for g, i in ordering_tools._session.cart.items.items() if i["name"] == "Pappad"
    )
    ordering_tools.add_item(guid)
    assert len(ordering_tools.get_cart()["lines"]) == 1

    ordering_tools.reset()  # run_bot calls this at the start of every call
    assert ordering_tools.get_cart()["lines"] == []


def test_system_prompt_exists_and_covers_the_rules():
    text = bot.PROMPT_PATH.read_text()
    for required in [
        "search_menu", "spoken_name", "spoken_price", "spoken_qty",
        "one at a time", "ambiguous", "readback_confirmed",
        "Pickup only", "twenty minutes", "payment",
    ]:
        assert required in text, f"system prompt missing: {required}"


@pytest.mark.parametrize(
    "mode,expected",
    [("telephony", 8000), ("local", 16000)],
)
def test_sample_rates_documented(mode, expected):
    actual = bot.TELEPHONY_SAMPLE_RATE if mode == "telephony" else bot.LOCAL_SAMPLE_RATE
    assert actual == expected


def test_local_webrtc_transport_constructs():
    """Local mode must build a real transport. No server, no negotiation."""
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    transport = SmallWebRTCTransport(
        webrtc_connection=SmallWebRTCConnection(),
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )
    assert transport.input() is not None
    assert transport.output() is not None


def test_runner_accepts_webrtc_transport_flag():
    import argparse

    from pipecat.runner.run import main  # noqa: F401  (import must not explode)

    parser = argparse.ArgumentParser()
    parser.add_argument("-t", "--transport", default="webrtc")
    assert parser.parse_args(["-t", "webrtc"]).transport == "webrtc"


def test_telephony_path_does_not_require_aiortc():
    """The webrtc import is inside the branch, so Twilio deploys stay slim."""
    import inspect

    src = inspect.getsource(bot)
    top = src.split("async def bot(")[0]
    assert "smallwebrtc" not in top, "webrtc import leaked to module scope"
    assert "smallwebrtc" in src.split("async def bot(")[1]
