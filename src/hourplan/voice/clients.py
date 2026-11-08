"""HTTP clients for the local speech-to-text and LLM servers (OpenAI-compatible protocol)."""

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from hourplan.voice.extract import Candidates, Option


class EngineError(RuntimeError):
    """A local engine is down, slow or answered something unusable."""


@dataclass(frozen=True)
class SttClient:
    url: str
    model: str
    timeout: float = 300.0
    transport: httpx.BaseTransport | None = None

    def transcribe(self, wav: bytes, *, prompt: str = "", language: str = "es") -> str:
        data = {"model": self.model, "language": language, "response_format": "json"}
        if prompt:
            data["prompt"] = prompt
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.post(
                    f"{self.url.rstrip('/')}/v1/audio/transcriptions",
                    data=data,
                    files={"file": ("dictado.wav", wav, "audio/wav")},
                )
            response.raise_for_status()
            text = response.json().get("text")
        except (httpx.HTTPError, ValueError, AttributeError) as err:
            raise EngineError("El servidor de transcripción no respondió bien.") from err
        if not isinstance(text, str):
            raise EngineError("El servidor de transcripción devolvió una respuesta inesperada.")
        return text.strip()


LINES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lines"],
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["date", "hours"],
                "properties": {
                    "date": {"type": "string"},
                    "hours": {"type": "number"},
                    "project_id": {"type": ["integer", "null"]},
                    "bucket_id": {"type": ["integer", "null"]},
                    "phase_id": {"type": ["integer", "null"]},
                    "task_type_id": {"type": ["integer", "null"]},
                    "application_id": {"type": ["integer", "null"]},
                    "description": {"type": ["string", "null"]},
                    "ai_assisted": {"type": "boolean"},
                },
            },
        }
    },
}

SYSTEM_PROMPT = (
    "Conviertes el dictado de una persona sobre sus horas de trabajo en líneas de registro. "
    'Responde solo JSON con la forma {"lines": [...]}. Una línea por bloque de trabajo. '
    "'date' es AAAA-MM-DD (usa la fecha de hoy si no dice otra); 'hours' son horas, en cuartos. "
    "Usa únicamente los ids de las listas dadas; si no hay coincidencia clara, pon null. "
    "Una línea tiene proyecto (project_id) o actividad (bucket_id), nunca ambos. "
    "'phase_id' debe ser una fase del proyecto elegido."
)


def _options(options: tuple[Option, ...]) -> list[dict[str, Any]]:
    return [
        {
            "id": o.id,
            **({"code": o.code} if o.code else {}),
            "name": o.label,
            **({"project_id": o.project_id} if o.project_id else {}),
        }
        for o in options
    ]


@dataclass(frozen=True)
class LlmClient:
    url: str
    model: str
    timeout: float = 90.0
    transport: httpx.BaseTransport | None = None

    def extract(self, text: str, today: date, candidates: Candidates) -> list[Any]:
        context = {
            "today": today.isoformat(),
            "projects": _options(candidates.projects),
            "phases": _options(candidates.phases),
            "buckets": _options(candidates.buckets),
            "task_types": _options(candidates.task_types),
            "applications": _options(candidates.applications),
        }
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False) + "\n\nDictado:\n" + text,
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "lines", "strict": True, "schema": LINES_SCHEMA},
            },
        }
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.post(f"{self.url.rstrip('/')}/v1/chat/completions", json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            lines = json.loads(content)["lines"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as err:
            raise EngineError("El modelo local no respondió bien.") from err
        if not isinstance(lines, list):
            raise EngineError("El modelo local devolvió una respuesta inesperada.")
        return lines
