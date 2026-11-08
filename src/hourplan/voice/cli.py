"""Worker and maintenance commands for the dictation queue."""

import hashlib
import os
import time
from datetime import datetime
from pathlib import Path

import click
import httpx
from flask import Blueprint, current_app

from hourplan.extensions import db
from hourplan.voice import services
from hourplan.voice.setup import engines_from

bp = Blueprint("voice_cli", __name__, cli_group=None)

PURGE_EVERY = 3600.0


def _engines() -> services.Engines:
    engines = engines_from(current_app.config)
    if engines is None:
        raise click.ClickException("Falta VOICE_STT_URL: el dictado por voz no está activado.")
    return engines


def work_once(engines: services.Engines) -> bool:
    """Process one queued dictation; returns whether there was one."""
    session = db.session
    now = services.utcnow()
    services.requeue_stale(session, now)
    dictation_id = services.claim_next(session, now)
    if dictation_id is None:
        return False
    services.process(session, dictation_id, engines, datetime.now().astimezone().date())
    return True


@bp.cli.command("voice-worker")
@click.option("--once", is_flag=True, help="Procesa lo que haya en cola y termina.")
@click.option("--poll", default=2.0, show_default=True, help="Segundos entre revisiones.")
def voice_worker_command(once: bool, poll: float) -> None:
    """Take dictations from the queue, transcribe them and store the drafts."""
    engines = _engines()
    last_purge = 0.0
    click.echo("Worker de voz en marcha.")
    while True:
        if work_once(engines):
            continue
        if once:
            return
        if time.monotonic() - last_purge > PURGE_EVERY:
            services.purge(db.session, services.utcnow())
            last_purge = time.monotonic()
        time.sleep(poll)


@bp.cli.command("voice-purge")
def voice_purge_command() -> None:
    """Blank the text of old unanswered dictations and delete the oldest finished ones."""
    count = services.purge(db.session, services.utcnow())
    click.echo(f"Dictados vaciados: {count}.")


@bp.cli.command("voice-bench")
@click.argument("wav", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def voice_bench_command(wav: Path) -> None:
    """Time the transcription of a WAV file (16 kHz mono) and print the text."""
    engines = _engines()
    data = wav.read_bytes()
    seconds = services.wav_duration(data, float(current_app.config["VOICE_MAX_SECONDS"]))
    started = time.monotonic()
    text = engines.stt.transcribe(data)
    took = time.monotonic() - started
    click.echo(f"Modelo: {engines.stt.model}")
    click.echo(
        f"Audio: {seconds:.1f} s · transcripción: {took:.1f} s · factor {took / seconds:.2f}x"
    )
    click.echo(f"Texto: {text}")


# Whisper models from the official ggml repository, pinned by SHA-256 (checked 2026-11-08).
_HF = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
WHISPER_MODELS = {
    "base": ("ggml-base.bin", "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"),
    "small": ("ggml-small.bin", "1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b"),
    "medium": (
        "ggml-medium.bin",
        "6c14d5adee5f86394037b4e4e8b59f1673b6cee10e3cf0b11bbdbee79c156208",
    ),
    "turbo": (
        "ggml-large-v3-turbo-q5_0.bin",
        "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
    ),
}


@bp.cli.command("voice-fetch-model")
@click.argument("name")
@click.option("--url", help="Descarga otro archivo (por ejemplo un GGUF para el LLM).")
@click.option("--sha256", "checksum", help="Suma SHA-256 esperada; obligatoria con --url.")
@click.option("--file", "filename", help="Nombre con el que se guarda; obligatorio con --url.")
def voice_fetch_model_command(
    name: str, url: str | None, checksum: str | None, filename: str | None
) -> None:
    """Download a model into VOICE_MODELS_DIR and verify its checksum before keeping it."""
    if url:
        if not checksum or not filename:
            raise click.ClickException("Con --url indica también --sha256 y --file.")
    elif name in WHISPER_MODELS:
        filename, checksum = WHISPER_MODELS[name]
        url = f"{_HF}/{filename}"
    else:
        raise click.ClickException(f"Modelo desconocido. Opciones: {', '.join(WHISPER_MODELS)}.")
    target_dir = Path(os.environ.get("VOICE_MODELS_DIR", "/models"))
    target = target_dir / Path(filename).name
    partial = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    click.echo(f"Descargando {filename}…")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as response:
        response.raise_for_status()
        with partial.open("wb") as out:
            for chunk in response.iter_bytes(1 << 20):
                digest.update(chunk)
                out.write(chunk)
    if digest.hexdigest() != checksum.lower():
        partial.unlink(missing_ok=True)
        raise click.ClickException("La suma SHA-256 no coincide; se descartó la descarga.")
    partial.replace(target)
    click.echo(f"Listo: {target} (SHA-256 verificado).")
