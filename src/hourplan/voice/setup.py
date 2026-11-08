"""Build the engine clients from the app settings; dictation is off without an STT server."""

from collections.abc import Mapping
from typing import Any

from hourplan.voice.clients import LlmClient, SttClient
from hourplan.voice.services import Engines


def enabled(config: Mapping[str, Any]) -> bool:
    return bool(config.get("VOICE_STT_URL"))


def engines_from(config: Mapping[str, Any]) -> Engines | None:
    if not enabled(config):
        return None
    llm = None
    if config.get("VOICE_LLM_URL"):
        llm = LlmClient(
            config["VOICE_LLM_URL"],
            config["VOICE_LLM_MODEL"],
            float(config["VOICE_LLM_TIMEOUT"]),
        )
    stt = SttClient(
        config["VOICE_STT_URL"], config["VOICE_STT_MODEL"], float(config["VOICE_STT_TIMEOUT"])
    )
    return Engines(stt, llm)
