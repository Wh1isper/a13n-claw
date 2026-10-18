"""Operator API acceptance using the actual Host lifecycle and native detached views."""

import httpx
import pytest
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from pydantic_ai.models.test import TestModel

from a13n_claw.app import create_app
from a13n_claw.bootstrap import initialize


@pytest.mark.anyio
async def test_operator_api_uses_one_host_and_exact_receipts(tmp_path, monkeypatch):
    monkeypatch.setenv("A13N_OFFICIAL_MODELS_AUTO_UPDATE", "0")

    async def resolve(self, model_id, *, thread_id):
        return TestModel(call_tools=[], custom_output_text="API reply")

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    workspace = tmp_path / "work"
    workspace.mkdir()
    root = tmp_path / "claw"
    await initialize(root, workspace, model_route="openai:gpt-4o")
    app = create_app(root=root, console=False)
    token = (root / "operator.token").read_text().strip()
    headers = {"Authorization": f"Bearer {token}"}
    async with app.router.lifespan_context(app):
        host_identity = id(app.state.service.host)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://claw"
        ) as client:
            assert (await client.get("/api/status")).status_code == 401
            assert (
                await client.get("/api/status", headers={**headers, "Origin": "http://other"})
            ).status_code == 403
            status = await client.get("/api/status", headers=headers)
            assert status.status_code == 200, status.text
            main = status.json()["instance"]["main_thread_id"]
            response = await client.post(
                f"/api/threads/{main}/inputs", headers=headers, json={"text": "Human CLI input"}
            )
            assert response.status_code == 202, response.text
            receipt = response.json()["receipt_id"]
            operation = await app.state.service.host.wait_root_operation(
                receipt, timeout_seconds=20
            )
            assert operation.status == "completed"
            saved = await client.get(f"/api/operations/{receipt}", headers=headers)
            assert saved.json()["receipt"]["thread_id"] == main
            history = await client.get(f"/api/threads/{main}/history", headers=headers)
            assert "API reply" in history.text
            sources = await client.get("/api/configuration", headers=headers)
            assert sources.status_code == 200
            source = (
                await client.get(
                    "/api/configuration/source",
                    params={"path": "agents/claw.yaml"},
                    headers=headers,
                )
            ).json()
            candidate = {
                "relative_path": source["relative_path"],
                "content": source["content"].replace("name: Claw", "name: Edited"),
                "expected_digest": source["source_digest"],
            }
            edit = await client.put("/api/configuration/source", json=candidate, headers=headers)
            assert edit.status_code == 200, edit.text
            assert (
                await client.put("/api/configuration/source", json=candidate, headers=headers)
            ).status_code == 409
            assert id(app.state.service.host) == host_identity
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://claw", headers=headers
        ) as client,
    ):
        assert (await client.get("/api/status")).json()["instance"]["main_thread_id"] == main
        assert (await client.get(f"/api/operations/{receipt}")).status_code == 409
        assert "API reply" in (await client.get(f"/api/threads/{main}/history")).text


@pytest.mark.anyio
async def test_uncertain_main_creation_keeps_operator_recovery_api_available(tmp_path, monkeypatch):
    from a13n_claw.business import BusinessStore
    from a13n_claw.settings import read_settings

    monkeypatch.setenv("A13N_OFFICIAL_MODELS_AUTO_UPDATE", "0")
    workspace = tmp_path / "work"
    workspace.mkdir()
    root = tmp_path / "claw"
    await initialize(root, workspace)
    settings = read_settings(root)
    store = BusinessStore(root / "business.sqlite3", settings)
    proposed = store.begin_target("main", settings.main_project_id)
    app = create_app(root=root, console=False)
    token = (root / "operator.token").read_text().strip()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://claw",
            headers={"Authorization": f"Bearer {token}"},
        ) as client,
    ):
        assert (await client.get("/api/status")).status_code == 200
        targets = (await client.get("/api/targets")).json()
        assert targets[0]["thread_id"] == proposed["thread_id"]
        assert targets[0]["status"] == "unknown"
        response = await client.post(
            "/api/targets/resolve",
            json={
                "key": "main",
                "action": "retry",
                "evidence": "Verified no matching Host Thread",
            },
        )
        assert response.status_code == 200, response.text
        async with app.state.service.lock:
            await app.state.service.ensure_target("main", settings.main_project_id)
        assert (await client.get("/api/status")).json()["instance"]["main_thread_id"] == proposed[
            "thread_id"
        ]
