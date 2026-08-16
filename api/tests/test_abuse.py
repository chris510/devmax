import asyncio
from collections.abc import AsyncIterator

import httpx
from fastapi import Depends, FastAPI, Request

from app.services import abuse, llm
from app.services.abuse import (
    AbuseProtectionMiddleware,
    FixedWindowLimiter,
    ProviderAdmission,
    provider_slot,
)
from tests.conftest import API_HEADERS, make_card


def _test_app(
    *,
    body_limit: int = 64,
    global_limit: int = 8,
    per_credential_limit: int = 2,
    request_global_limit: int = 64,
    request_per_credential_limit: int = 4,
) -> FastAPI:
    app = FastAPI()
    paid_admission = ProviderAdmission(
        global_limit=global_limit,
        per_credential_limit=per_credential_limit,
    )
    app.add_middleware(
        AbuseProtectionMiddleware,
        default_body_bytes=body_limit,
        limiter=FixedWindowLimiter(),
        request_admission=ProviderAdmission(
            global_limit=request_global_limit,
            per_credential_limit=request_per_credential_limit,
        ),
    )

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, str]:
        return {"body": (await request.body()).decode()}

    async def admitted(request: Request) -> AsyncIterator[None]:
        user_id = request.headers.get("X-Resolved-User", "account")
        async with provider_slot(user_id, admission=paid_admission):
            yield

    @app.post("/sessions/{session_id}/answers", dependencies=[Depends(admitted)])
    async def paid(session_id: str) -> dict[str, str]:
        await asyncio.sleep(0.05)
        return {"session_id": session_id}

    return app


async def test_body_is_replayed_after_pre_routing_size_check() -> None:
    transport = httpx.ASGITransport(app=_test_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/echo", content=b"safe")

    assert response.status_code == 200
    assert response.json() == {"body": "safe"}


async def test_oversized_body_is_rejected_before_route() -> None:
    transport = httpx.ASGITransport(app=_test_app(body_limit=4))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/echo", content=b"12345")

    assert response.status_code == 413
    assert response.json() == {"detail": "request_too_large"}


async def test_provider_admission_rejects_instead_of_queueing() -> None:
    transport = httpx.ASGITransport(
        app=_test_app(global_limit=1, per_credential_limit=1)
    )
    headers = {"X-Resolved-User": "same-account"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(
            client.post("/sessions/a/answers", headers=headers, json={})
        )
        await asyncio.sleep(0.01)
        second = await client.post("/sessions/b/answers", headers=headers, json={})
        first_response = await first

    assert first_response.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": "provider_busy"}
    assert second.headers["retry-after"] == "1"


async def test_provider_slot_is_released_after_request() -> None:
    transport = httpx.ASGITransport(
        app=_test_app(global_limit=1, per_credential_limit=1)
    )
    headers = {"X-Resolved-User": "same-account"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/sessions/a/answers", headers=headers, json={})
        second = await client.post("/sessions/b/answers", headers=headers, json={})

    assert first.status_code == 200
    assert second.status_code == 200


async def test_slow_unresolved_body_does_not_reserve_a_provider_slot() -> None:
    body_started = asyncio.Event()
    release_body = asyncio.Event()

    async def slow_body():
        body_started.set()
        await release_body.wait()
        yield b"{}"

    transport = httpx.ASGITransport(
        app=_test_app(global_limit=1, per_credential_limit=1)
    )
    headers = {"X-Resolved-User": "first-account"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(
            client.post("/sessions/a/answers", headers=headers, content=slow_body())
        )
        await asyncio.wait_for(body_started.wait(), timeout=1)
        second = await client.post(
            "/sessions/b/answers",
            headers={"X-Resolved-User": "another-account"},
            json={},
        )
        release_body.set()
        first_response = await first

    assert first_response.status_code == 200
    assert second.status_code == 200


async def test_duplicate_credential_headers_are_rejected() -> None:
    transport = httpx.ASGITransport(app=_test_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/echo",
            headers=[
                ("Authorization", "Bearer first"),
                ("Authorization", "Bearer second"),
            ],
            content=b"safe",
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "duplicate_credential_header"}


async def test_internal_provider_worker_waits_for_a_released_slot() -> None:
    admission = ProviderAdmission(global_limit=1, per_credential_limit=1)
    assert await admission.try_enter("interactive")

    waiter = asyncio.create_task(admission.wait_enter("background"))
    await asyncio.sleep(0)
    assert not waiter.done()

    await admission.leave("interactive")
    await asyncio.wait_for(waiter, timeout=1)
    await admission.leave("background")


async def test_invalid_slow_bearer_never_reserves_paid_admission(
    client, monkeypatch
) -> None:
    admission = ProviderAdmission(global_limit=1, per_credential_limit=1)
    monkeypatch.setattr(abuse, "_paid_provider_admission", admission)
    body_started = asyncio.Event()
    release_body = asyncio.Event()

    async def slow_body():
        body_started.set()
        await release_body.wait()
        yield b'{}'

    request = asyncio.create_task(
        client.post(
            "/sessions/00000000-0000-0000-0000-000000000000/answers",
            headers={"Authorization": "Bearer not-a-real-token"},
            content=slow_body(),
        )
    )
    await asyncio.wait_for(body_started.wait(), timeout=1)
    assert await admission.try_enter("authenticated-user")
    await admission.leave("authenticated-user")
    release_body.set()

    response = await request
    assert response.status_code == 401


async def test_real_paid_route_uses_resolved_account_admission(
    client, db, monkeypatch
) -> None:
    admission = ProviderAdmission(global_limit=1, per_credential_limit=1)
    monkeypatch.setattr(abuse, "_paid_provider_admission", admission)
    provider_started = asyncio.Event()
    release_provider = asyncio.Event()

    async def paused_question(**_kwargs):
        provider_started.set()
        await release_provider.wait()
        return "What changes when the membership set changes?"

    monkeypatch.setattr(llm, "generate_question", paused_question)
    first_card = make_card(canonical_question=None)
    second_card = make_card(topic="Raft membership", canonical_question=None)
    db.add(first_card)
    db.add(second_card)
    await db.commit()

    first = asyncio.create_task(
        client.post(f"/cards/{first_card.id}/sessions", headers=API_HEADERS)
    )
    await asyncio.wait_for(provider_started.wait(), timeout=1)
    second = await client.post(
        f"/cards/{second_card.id}/sessions", headers=API_HEADERS
    )
    release_provider.set()
    first_response = await first

    assert first_response.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": "provider_busy"}


async def test_general_write_admission_bounds_slow_unresolved_credentials() -> None:
    body_started = asyncio.Event()
    release_body = asyncio.Event()

    async def slow_body():
        body_started.set()
        await release_body.wait()
        yield b"safe"

    transport = httpx.ASGITransport(
        app=_test_app(request_global_limit=1, request_per_credential_limit=1)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = asyncio.create_task(client.post("/echo", content=slow_body()))
        await asyncio.wait_for(body_started.wait(), timeout=1)
        second = await client.post("/echo", content=b"safe")
        release_body.set()
        first_response = await first

    assert first_response.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": "server_busy"}


async def test_real_app_rejects_oversized_answer_before_database_work(client) -> None:
    response = await client.post(
        "/sessions/00000000-0000-0000-0000-000000000000/answers",
        headers=API_HEADERS,
        json={"text": "x" * (129 * 1024)},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "request_too_large"}
