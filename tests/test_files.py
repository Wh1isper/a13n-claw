import json
import time
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from test_api import authorize, completed, configure, models

from a13n_claw.app import create_app
from a13n_claw.domain import MAX_ASSET_BYTES, ClawError, InputRequest, Principal
from a13n_claw.storage import Store


def test_retained_file_retry_scope_and_immutable_bytes(runtime_store: Store):
    store = runtime_store
    first = store.create_thread("operator", "Files", "default")
    second = store.create_thread("operator", "Other", "default")
    saved = store.retain_asset("operator", first.id, "upload", "input.txt", "text/plain", b"hello")
    assert (
        store.retain_asset("operator", first.id, "upload", "input.txt", "text/plain", b"hello")
        == saved
    )
    with pytest.raises(ClawError, match="identity"):
        store.retain_asset("operator", first.id, "upload", "input.txt", "text/plain", b"changed")
    with pytest.raises(ClawError, match="8 MiB"):
        store.retain_asset(
            "operator", first.id, "large", "input.txt", "text/plain", b"x" * (MAX_ASSET_BYTES + 1)
        )
    with pytest.raises(ClawError, match="belong"):
        store.admit(
            "operator",
            second.id,
            InputRequest(request_id="wrong", text="wrong", attachment_ids=(saved.id,)),
            "/work",
        )
    assert store.runs("operator", second.id) == []
    store.set_principal(
        "operator", Principal(id="reader", thread_ids=(second.id,), actions=("read",)), "hash"
    )
    with pytest.raises(ClawError):
        store.asset_content("reader", saved.id)
    assert Store(store.path).asset_content("operator", saved.id)[1] == b"hello"


def test_http_files_are_selected_environment_reads_and_scoped_downloads(console, tmp_path):
    (tmp_path / "workspace" / "source.txt").write_text("workspace content")
    with TestClient(create_app(models=models)) as client:
        authorize(client, tmp_path)
        configure(client)
        thread = client.post("/api/threads", json={"request_id": "create", "title": "Files"}).json()
        uploaded = client.post(
            f"/api/threads/{thread['id']}/files?request_id=upload&name=sample.html",
            content=b"<script>untrusted()</script>",
            headers={"Content-Type": "text/html"},
        )
        assert uploaded.status_code == 201, uploaded.text
        asset = uploaded.json()
        response = client.get(f"/api/files/{asset['id']}/download")
        assert response.content == b"<script>untrusted()</script>"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["content-type"] == "application/octet-stream"
        receipt = client.post(
            f"/api/threads/{thread['id']}/inputs",
            json={
                "request_id": "read",
                "text": "read",
                "attachment_ids": [asset["id"]],
            },
        ).json()
        assert receipt["attachment_ids"] == [asset["id"]]
        result = completed(client, receipt["run_id"])
        files = client.get(f"/api/runs/{result['id']}/files")
        assert files.status_code == 200, files.text
        assert "source.txt" in files.text
        text = client.get(
            f"/api/runs/{result['id']}/file-content", params={"path": "/workspace/source.txt"}
        )
        assert text.status_code == 200, text.text
        assert text.json()["text"] == "workspace content"
        assert (
            client.get(
                f"/api/runs/{result['id']}/file-content",
                params={"path": str(tmp_path / "data" / "operator.token")},
            ).status_code
            == 409
        )
        target = client.get(f"/api/threads/{thread['id']}/targets").json()[0]
        client.post(f"/api/targets/{target['id']}/manage", json={"operation": "remove"})
        assert client.get(f"/api/runs/{result['id']}/files").status_code == 409
        assert client.get(f"/api/files/{asset['id']}/download").content == response.content


@pytest.mark.parametrize("mode", ["allow", "ask"])
def test_native_agent_reads_attachment_and_retains_workspace_artifact(console, tmp_path, mode):
    (tmp_path / "workspace" / "result.txt").write_text("finished bytes")

    @asynccontextmanager
    async def fixture_models(definition, credential):
        async def model(messages, info):
            results = [
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            if not results:
                prompt = next(
                    part.content
                    for message in messages
                    if isinstance(message, ModelRequest)
                    for part in message.parts
                    if isinstance(part, UserPromptPart)
                    and isinstance(part.content, str)
                    and part.content.startswith('[{"attachment_ids"')
                )
                attachment = json.loads(prompt)[0]["attachment_ids"][0]
                yield {
                    0: DeltaToolCall(
                        name="read_retained_file",
                        json_args=json.dumps({"file_id": attachment}),
                        tool_call_id="read",
                    )
                }
            elif len(results) == 1:
                assert results[0].content == "attachment content"
                yield {
                    0: DeltaToolCall(
                        name="retain_artifact",
                        json_args=json.dumps(
                            {
                                "path": "/workspace/result.txt",
                                "name": "result.txt",
                                "media_type": "text/plain",
                            }
                        ),
                        tool_call_id="retain",
                    )
                }
            else:
                yield "Artifact saved"

        yield FunctionModel(stream_function=model)

    with TestClient(create_app(models=fixture_models)) as client:
        authorize(client, tmp_path)
        configure(client)
        assert (
            client.post(
                "/api/resources/import",
                json={
                    "resources": [
                        {
                            "kind": "profile",
                            "id": "default",
                            "expected_version": 1,
                            "content": {
                                "model_id": "model",
                                "environment_id": "local",
                                "permissions": {"rules": {"claw.files.retain": mode}},
                            },
                        }
                    ]
                },
            ).status_code
            == 200
        )
        thread = client.post(
            "/api/threads", json={"request_id": "create", "title": "Agent files"}
        ).json()
        file = client.post(
            f"/api/threads/{thread['id']}/files?request_id=input&name=input.txt",
            content=b"attachment content",
            headers={"Content-Type": "text/plain"},
        ).json()
        receipt = client.post(
            f"/api/threads/{thread['id']}/inputs",
            json={
                "request_id": "work",
                "text": "Read then retain",
                "attachment_ids": [file["id"]],
            },
        ).json()
        if mode == "ask":
            deadline = time.monotonic() + 10
            decisions = []
            while not decisions and time.monotonic() < deadline:
                decisions = client.get(f"/api/runs/{receipt['run_id']}/decisions").json()
                time.sleep(0.02)
            assert decisions
            assert len(client.get(f"/api/threads/{thread['id']}/files").json()) == 1
            answer = client.post(
                f"/api/decisions/{decisions[0]['id']}/answer", json={"approvals": {"retain": True}}
            )
            assert answer.status_code == 200, answer.text
        completed(client, receipt["run_id"])
        artifacts = [
            item
            for item in client.get(f"/api/threads/{thread['id']}/files").json()
            if item["kind"] == "artifact"
        ]
        assert len(artifacts) == 1
        (tmp_path / "workspace" / "result.txt").write_text("later mutation")
        assert client.get(f"/api/files/{artifacts[0]['id']}/download").content == b"finished bytes"
