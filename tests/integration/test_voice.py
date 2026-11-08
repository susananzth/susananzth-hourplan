import io
import json
import wave
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from flask import Flask
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan import create_app
from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.catalogs.models import CatalogItem, CatalogKind
from hourplan.projects.models import Project
from hourplan.resources.models import Resource
from hourplan.timesheet.models import EntrySource, TimeEntry
from hourplan.voice import cli, services
from hourplan.voice.clients import EngineError, LlmClient, SttClient
from hourplan.voice.models import DictationStatus, VoiceDictation
from tests.conftest import csrf_token

TODAY = date(2026, 11, 9)
NOW = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)


def make_wav(seconds: float = 2.0, *, rate: int = 16_000, channels: int = 1) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * int(rate * seconds) * channels)
    return out.getvalue()


def stt_transport(
    text: str = "ayer dos horas en PJ-00001", status: int = 200
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/transcriptions"
        assert b"dictado.wav" in request.content
        return httpx.Response(status, json={"text": text})

    return httpx.MockTransport(handler)


def llm_transport(lines: list[dict] | str, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        content = lines if isinstance(lines, str) else json.dumps({"lines": lines})
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler)


def engines(stt: httpx.MockTransport, llm: httpx.MockTransport | None = None) -> services.Engines:
    return services.Engines(
        SttClient("http://stt.test", "m-stt", transport=stt),
        LlmClient("http://llm.test", "m-llm", transport=llm) if llm else None,
    )


@pytest.fixture
def resource(session: Session) -> Resource:
    item = Resource(display_name="Ana")
    session.add(item)
    session.commit()
    return item


@pytest.fixture
def project(session: Session) -> Project:
    item = Project(code="PJ-00001", title="Portal de clientes")
    session.add(item)
    session.commit()
    return item


@pytest.fixture
def bucket(session: Session) -> CatalogItem:
    item = CatalogItem(kind=CatalogKind.ACTIVITY_BUCKET.value, code="TRAIN", name="Capacitación")
    session.add(item)
    session.commit()
    return item


# ------------------------------------------------------------------------------- audio


def test_wav_validation() -> None:
    assert services.wav_duration(make_wav(2), 180) == 2
    for bad in (b"no es audio", make_wav(rate=8_000), make_wav(channels=2), make_wav(0.1)):
        with pytest.raises(services.VoiceError):
            services.wav_duration(bad, 180)
    with pytest.raises(services.VoiceError, match="hasta 1 minutos"):
        services.wav_duration(make_wav(70), 60)


# ------------------------------------------------------------------------------ clients


def test_stt_client_errors_become_engine_errors() -> None:
    client = SttClient("http://stt.test", "m", transport=stt_transport(status=500))
    with pytest.raises(EngineError):
        client.transcribe(make_wav())
    odd = SttClient(
        "http://stt.test",
        "m",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"nope": 1})),
    )
    with pytest.raises(EngineError):
        odd.transcribe(make_wav())


def test_llm_client_rejects_garbage() -> None:
    cands = services.extract.Candidates()
    for transport in (llm_transport("no es json"), llm_transport([], status=503)):
        client = LlmClient("http://llm.test", "m", transport=transport)
        if transport is not None:
            with pytest.raises(EngineError):
                client.extract("x", TODAY, cands)


# ------------------------------------------------------------------------------- queue


def queue(session: Session, resource: Resource) -> VoiceDictation:
    return services.create_dictation(
        session, resource.id, make_wav(), actor_id=None, max_seconds=180
    )


def test_process_makes_a_draft_and_erases_the_audio(
    session: Session, resource: Resource, project: Project
) -> None:
    item = queue(session, resource)
    assert services.claim_next(session, NOW) == item.id
    assert services.claim_next(session, NOW) is None
    services.process(session, item.id, engines(stt_transport()), TODAY)
    session.refresh(item)
    assert item.status == DictationStatus.READY
    assert item.audio is None
    assert item.transcript == "ayer dos horas en PJ-00001"
    assert item.stt_model == "m-stt"
    assert item.llm_model is None
    [line] = item.draft
    assert line["project_id"] == project.id
    assert line["hours"] == "2.00" or line["hours"] == "2"
    assert line["work_date"] == "2026-11-08"


def test_llm_refines_but_unknown_ids_are_ignored(
    session: Session, resource: Resource, project: Project
) -> None:
    item = queue(session, resource)
    services.claim_next(session, NOW)
    llm = llm_transport(
        [{"date": "2026-11-08", "hours": 2, "project_id": 9999, "description": "Pruebas"}]
    )
    services.process(session, item.id, engines(stt_transport(), llm), TODAY)
    session.refresh(item)
    [line] = item.draft
    assert line["project_id"] == project.id  # the model's unknown id loses to the rules
    assert line["description"] == "Pruebas"
    assert item.llm_model == "m-llm"


def test_llm_down_leaves_the_rules_draft_with_a_notice(
    session: Session, resource: Resource, project: Project
) -> None:
    item = queue(session, resource)
    services.claim_next(session, NOW)
    services.process(session, item.id, engines(stt_transport(), llm_transport("x")), TODAY)
    session.refresh(item)
    assert item.status == DictationStatus.READY
    assert any("reglas" in note for note in item.draft[0]["notes"])
    assert item.llm_model is None


def test_stt_failure_retries_then_fails_and_erases_the_audio(
    session: Session, resource: Resource
) -> None:
    item = queue(session, resource)
    broken = engines(stt_transport(status=500))
    for attempt in range(1, services.MAX_ATTEMPTS + 1):
        assert services.claim_next(session, NOW) == item.id
        services.process(session, item.id, broken, TODAY)
        session.refresh(item)
        assert item.attempts == attempt
        if attempt < services.MAX_ATTEMPTS:
            assert item.status == DictationStatus.QUEUED
            assert item.audio is not None
    assert item.status == DictationStatus.FAILED
    assert item.audio is None
    assert item.error


def test_stale_processing_goes_back_to_the_queue(session: Session, resource: Resource) -> None:
    item = queue(session, resource)
    services.claim_next(session, NOW)
    assert services.requeue_stale(session, NOW) == 0
    assert services.requeue_stale(session, NOW + services.STALE_AFTER + timedelta(minutes=1)) == 1
    session.refresh(item)
    assert item.status == DictationStatus.QUEUED


def test_purge_blanks_old_unanswered_and_deletes_old_finished(
    session: Session, resource: Resource
) -> None:
    stale = queue(session, resource)
    stale.status = DictationStatus.READY
    stale.transcript = "texto"
    stale.draft = [{"hours": "1"}]
    stale.created_at = NOW - timedelta(days=8)
    old = queue(session, resource)
    old.status = DictationStatus.CONFIRMED
    old.created_at = NOW - timedelta(days=91)
    fresh = queue(session, resource)
    session.commit()
    stale_id, old_id, fresh_id = stale.id, old.id, fresh.id
    services.purge(session, NOW)
    session.expire_all()
    assert session.get(VoiceDictation, old_id) is None
    blanked = session.get(VoiceDictation, stale_id)
    assert blanked.status == DictationStatus.DISCARDED
    assert blanked.transcript is None
    assert blanked.draft is None
    assert session.get(VoiceDictation, fresh_id).status == DictationStatus.QUEUED


# -------------------------------------------------------------------------- confirming


def ready(session: Session, resource: Resource, rows: list[dict]) -> VoiceDictation:
    item = queue(session, resource)
    item.status = DictationStatus.READY
    item.audio = None
    item.transcript = "texto"
    item.draft = rows
    session.commit()
    return item


def test_confirm_saves_voice_entries_and_clears_the_text(
    session: Session, resource: Resource, project: Project, bucket: CatalogItem
) -> None:
    item = ready(session, resource, [])
    saved = services.confirm(
        session,
        item.id,
        resource.id,
        [
            {"work_date": "2026-11-08", "hours": "2,5", "project_id": project.id},
            {
                "work_date": "2026-11-08",
                "hours": "1",
                "activity_bucket_id": bucket.id,
                "description": "Curso",
                "ai_assisted": True,
            },
        ],
        actor_id=None,
    )
    assert saved == 2
    entries = session.scalars(select(TimeEntry).order_by(TimeEntry.id)).all()
    assert [e.hours for e in entries] == [Decimal("2.50"), Decimal("1.00")]
    assert {e.source for e in entries} == {EntrySource.VOICE}
    session.refresh(item)
    assert item.status == DictationStatus.CONFIRMED
    assert item.transcript is None
    assert item.draft is None
    with pytest.raises(services.VoiceError, match="ya no está listo"):
        services.confirm(session, item.id, resource.id, [{}], actor_id=None)


def test_confirm_saves_nothing_if_one_line_is_wrong(
    session: Session, resource: Resource, project: Project
) -> None:
    item = ready(session, resource, [])
    rows = [
        {"work_date": "2026-11-08", "hours": "1", "project_id": project.id},
        {"work_date": "2026-11-08", "hours": "1"},  # no project or activity
    ]
    with pytest.raises(services.VoiceError, match="Línea 2"):
        services.confirm(session, item.id, resource.id, rows, actor_id=None)
    session.rollback()
    assert session.scalars(select(TimeEntry)).all() == []
    assert session.get(VoiceDictation, item.id).status == DictationStatus.READY
    with pytest.raises(services.VoiceError, match="al menos una"):
        services.confirm(session, item.id, resource.id, [], actor_id=None)


def test_confirm_checks_the_owner(session: Session, resource: Resource, project: Project) -> None:
    item = ready(session, resource, [])
    with pytest.raises(services.VoiceError):
        services.confirm(session, item.id, resource.id + 1, [{}], actor_id=None)


def test_worker_loop_step(
    session: Session, app: Flask, resource: Resource, project: Project
) -> None:
    app.config["VOICE_STT_URL"] = "http://stt.test"
    assert cli.work_once(engines(stt_transport())) is False
    item = queue(session, resource)
    assert cli.work_once(engines(stt_transport())) is True
    session.refresh(item)
    assert item.status == DictationStatus.READY


# ------------------------------------------------------------------------------- views


@pytest.fixture
def voice_on(app: Flask) -> None:
    app.config["VOICE_STT_URL"] = "http://stt.test"


@pytest.fixture
def me(
    session: Session,
    voice_on: None,
    make_user: Callable[..., User],
    login: Callable[..., TestResponse],
) -> Resource:
    user = make_user()
    auth.grant_role(session, user, Role.DEVELOPER, actor_id=None)
    item = Resource(display_name="Ana", user_id=user.id)
    session.add(item)
    session.commit()
    assert login(user.email).status_code == 302
    return item


def upload(client, wav: bytes):  # type: ignore[no-untyped-def]
    return client.post(
        "/voz/dictados",
        data=wav,
        content_type="audio/wav",
        headers={"X-CSRFToken": csrf_token(client, "/voz")},
    )


def test_feature_is_hidden_when_not_configured(
    client,
    app: Flask,
    session: Session,
    make_user,
    login,  # type: ignore[no-untyped-def]
) -> None:
    user = make_user()
    auth.grant_role(session, user, Role.DEVELOPER, actor_id=None)
    session.add(Resource(display_name="Ana", user_id=user.id))
    session.commit()
    login(user.email)
    assert client.get("/voz").status_code == 404


def test_upload_and_review_flow(
    client,
    me: Resource,
    session: Session,
    project: Project,  # type: ignore[no-untyped-def]
) -> None:
    page = client.get("/voz")
    assert page.status_code == 200
    assert b"data-voice-recorder" in page.data

    created = upload(client, make_wav())
    assert created.status_code == 201
    url = created.get_json()["url"]
    item = session.scalars(select(VoiceDictation)).one()
    assert item.resource_id == me.id
    assert item.status == DictationStatus.QUEUED
    assert b"Esperando turno" in client.get(url + "/estado").data

    services.claim_next(session, NOW)
    services.process(session, item.id, engines(stt_transport()), TODAY)
    form = client.get(url)
    html = form.get_data(as_text=True)
    assert "ayer dos horas en PJ-00001" in html
    assert f'value="p:{project.id}" selected' in html

    done = client.post(
        f"{url}/confirmar",
        data={
            "csrf_token": csrf_token(client, "/voz"),
            "n": "1",
            "l0-usar": "1",
            "l0-fecha": "2026-11-08",
            "l0-horas": "2",
            "l0-destino": f"p:{project.id}",
            "l0-desc": "Pruebas",
        },
    )
    assert done.status_code == 302
    entry = session.scalars(select(TimeEntry)).one()
    assert entry.source == EntrySource.VOICE
    assert entry.description == "Pruebas"


def test_bad_upload_is_refused(client, me: Resource, session: Session) -> None:  # type: ignore[no-untyped-def]
    refused = upload(client, b"basura")
    assert refused.status_code == 400
    assert "formato" in refused.get_json()["error"]
    assert session.scalars(select(VoiceDictation)).all() == []


def test_confirm_error_keeps_the_edits(
    client,
    me: Resource,
    session: Session,
    project: Project,  # type: ignore[no-untyped-def]
) -> None:
    item = ready(session, me, [{"work_date": "2026-11-08", "hours": "1", "notes": []}])
    response = client.post(
        f"/voz/dictados/{item.id}/confirmar",
        data={
            "csrf_token": csrf_token(client, "/voz"),
            "n": "1",
            "l0-usar": "1",
            "l0-fecha": "2026-11-08",
            "l0-horas": "3",
            "l0-desc": "Lo que escribí",
        },
    )
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert "Línea 1" in html
    assert "Lo que escribí" in html
    assert session.scalars(select(TimeEntry)).all() == []


def test_discard_and_isolation(
    client,
    me: Resource,
    session: Session,
    make_user,
    project: Project,  # type: ignore[no-untyped-def]
) -> None:
    other = Resource(display_name="Otra")
    session.add(other)
    session.commit()
    foreign = ready(session, other, [])
    assert client.get(f"/voz/dictados/{foreign.id}").status_code == 404
    mine = ready(session, me, [])
    token = csrf_token(client, "/voz")
    assert (
        client.post(f"/voz/dictados/{foreign.id}/descartar", data={"csrf_token": token}).status_code
        == 404
    )
    assert (
        client.post(f"/voz/dictados/{mine.id}/descartar", data={"csrf_token": token}).status_code
        == 302
    )
    session.refresh(mine)
    assert mine.status == DictationStatus.DISCARDED
    assert mine.transcript is None


def test_observer_cannot_dictate(
    client,
    voice_on: None,
    session: Session,
    make_user,
    login,  # type: ignore[no-untyped-def]
) -> None:
    user = make_user()
    auth.grant_role(session, user, Role.OBSERVER, actor_id=None)
    login(user.email)
    assert client.get("/voz").status_code == 403


def test_microphone_is_allowed_only_when_voice_is_on() -> None:
    off = create_app("testing").test_client().get("/healthz")
    assert "microphone=()" in off.headers["Permissions-Policy"]
    on = create_app("testing", {"VOICE_STT_URL": "http://stt.test"}).test_client().get("/healthz")
    assert "microphone=(self)" in on.headers["Permissions-Policy"]


# ---------------------------------------------------------------------- model download


class _FakeStream:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self, size: int):  # type: ignore[no-untyped-def]
        yield self.payload


def test_fetch_model_checks_the_checksum(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    import hashlib

    payload = b"modelo falso"
    monkeypatch.setattr(cli.httpx, "stream", lambda *a, **k: _FakeStream(payload))
    monkeypatch.setenv("VOICE_MODELS_DIR", str(tmp_path))
    runner = app.test_cli_runner()
    good = hashlib.sha256(payload).hexdigest()
    args = ["voice-fetch-model", "x", "--url", "http://m.test/a.gguf", "--file", "a.gguf"]
    ok = runner.invoke(args=[*args, "--sha256", good])
    assert ok.exit_code == 0, ok.output
    assert (tmp_path / "a.gguf").read_bytes() == payload
    bad = runner.invoke(args=[*args, "--sha256", "0" * 64])
    assert bad.exit_code != 0
    assert "no coincide" in bad.output
    assert list(tmp_path.glob("*.part")) == []
    assert runner.invoke(args=["voice-fetch-model", "enorme"]).exit_code != 0
