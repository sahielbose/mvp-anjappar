"""Offline checks that bot.py wires the ordering tools correctly.

No network, no audio, no API calls.
"""

import asyncio
import inspect

import pytest
from pipecat.services.llm_service import FunctionCallParams

import bot
from ordering import tools as ordering_tools


def make_llm(session):
    llm = bot.OpenAILLMService(api_key="test-key-not-used")
    bot.register_ordering_tools(llm, session)
    return llm


def invoke(llm, name, arguments):
    """Call a registered handler the way pipecat would, return its result."""
    captured = {}

    async def result_callback(result, *args, **kwargs):
        captured["result"] = result

    params = FunctionCallParams(
        function_name=name,
        tool_call_id="call_1",
        arguments=arguments,
        llm=llm,
        context=None,
        result_callback=result_callback,
    )
    asyncio.run(llm._functions[name].handler(params))
    return captured["result"]


def guid_for(session, name):
    return next(g for g, i in session.cart.items.items() if i["name"] == name)


def test_all_eight_tools_are_registered_on_the_llm():
    llm = make_llm(ordering_tools.Session())
    for schema in ordering_tools.TOOL_SCHEMAS:
        assert llm.has_function(schema["function"]["name"])


def test_handlers_are_not_detected_as_the_deprecated_signature():
    """pipecat calls any handler with >1 parameter using the legacy
    6-positional-arg form, which would break every tool call at runtime."""
    llm = make_llm(ordering_tools.Session())
    for name, item in llm._functions.items():
        assert item.handler_deprecated is False, name
        assert len(inspect.signature(item.handler).parameters) == 1, name


def test_registry_matches_the_published_schemas():
    schema_names = {s["function"]["name"] for s in ordering_tools.TOOL_SCHEMAS}
    assert set(ordering_tools.Session().tool_handlers()) == schema_names
    assert len(schema_names) == 8


def test_handler_routes_to_the_real_tool_and_returns_its_dict():
    llm = make_llm(ordering_tools.Session())
    result = invoke(llm, "search_menu", {"query": "chettinaad chicken"})
    assert result["candidates"][0]["name"] == "Chettinad Chicken Masala"
    assert result["candidates"][0]["spoken_price"] == "fourteen dollars"


def test_each_handler_hits_its_own_tool():
    """The registration loop must not close over the last handler."""
    llm = make_llm(ordering_tools.Session())
    assert invoke(llm, "get_cart", {})["lines"] == []
    assert invoke(llm, "submit_order", {})["error"] == "EMPTY_CART"


# -- concurrency: one cart per connection -----------------------------------


def test_two_sessions_do_not_share_a_cart():
    """Two simultaneous callers must not see each other's order."""
    a, b = ordering_tools.Session(), ordering_tools.Session()

    a.add_item(guid_for(a, "Masala Dosa (V)"))
    a.set_customer_name("Priya")

    assert len(a.get_cart()["lines"]) == 1
    assert b.get_cart()["lines"] == []
    assert b.get_cart()["customer_name"] is None


def test_registered_handlers_are_bound_to_their_own_session():
    """Handlers must not resolve the cart globally at call time."""
    a, b = ordering_tools.Session(), ordering_tools.Session()
    llm_a, llm_b = make_llm(a), make_llm(b)

    invoke(llm_a, "add_item", {"item_guid": guid_for(a, "Masala Dosa (V)")})
    invoke(llm_a, "set_customer_name", {"name": "Priya"})

    assert len(invoke(llm_a, "get_cart", {})["lines"]) == 1
    assert invoke(llm_b, "get_cart", {})["lines"] == []
    assert invoke(llm_b, "submit_order", {})["error"] == "EMPTY_CART"


def test_handlers_ignore_the_module_level_session():
    """bot.py's sessions must be unaffected by the module-level convenience one."""
    ordering_tools.reset()
    per_call = ordering_tools.Session()
    llm = make_llm(per_call)

    ordering_tools.add_item(guid_for(per_call, "Pappad"))  # module-level session

    assert invoke(llm, "get_cart", {})["lines"] == []


def test_bot_builds_a_session_per_connection():
    """run_bot must construct its own Session, not reach for module state."""
    src = inspect.getsource(bot.run_bot)
    assert "ordering_tools.Session(" in src
    assert "ordering_tools.reset()" not in src


# -- prompt and config -------------------------------------------------------


def test_system_prompt_exists_and_covers_the_rules():
    text = bot.PROMPT_PATH.read_text()
    for required in [
        "search_menu", "spoken_name", "spoken_price", "spoken_qty",
        "one at a time", "ambiguous", "readback_confirmed",
        "Pickup only", "twenty minutes", "payment",
        "set_customer_name", "pickup code", "first name",
    ]:
        assert required in text, f"system prompt missing: {required}"


@pytest.mark.parametrize("mode,expected", [("telephony", 8000), ("local", 16000)])
def test_sample_rates_documented(mode, expected):
    actual = bot.TELEPHONY_SAMPLE_RATE if mode == "telephony" else bot.LOCAL_SAMPLE_RATE
    assert actual == expected


def test_local_webrtc_transport_constructs():
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    transport = SmallWebRTCTransport(
        webrtc_connection=SmallWebRTCConnection(),
        params=TransportParams(
            audio_in_enabled=True, audio_out_enabled=True, vad_analyzer=SileroVADAnalyzer()
        ),
    )
    assert transport.input() is not None
    assert transport.output() is not None


def test_telephony_path_does_not_require_aiortc():
    """The webrtc import is inside the branch, so Twilio deploys stay slim."""
    src = inspect.getsource(bot)
    assert "smallwebrtc" not in src.split("async def bot(")[0]
    assert "smallwebrtc" in src.split("async def bot(")[1]
