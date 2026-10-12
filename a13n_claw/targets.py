"""Claw-owned target lifecycle and fixed, execution-only Harness sources."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import closing
from dataclasses import dataclass
from typing import Literal, Protocol

import docker
from a13n_harness.environment import (
    EnvironmentConnector,
    EnvironmentDescriptor,
    EnvironmentState,
)
from a13n_harness.environment.models import decode_target_state
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
)
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
from a13n_harness.providers.environment.docker.configuration import (
    DockerEnvironmentConfiguration,
    DockerProviderStateData,
)
from a13n_harness.providers.environment.docker.provider import DOCKER, DockerConnectionConfiguration
from docker.errors import ImageNotFound, NotFound
from docker.models.containers import Container
from docker.types import Mount

from a13n_claw.async_utils import settle_on_cancel
from a13n_claw.domain import ClawError, TargetRecord

TargetStatus = Literal["running", "stopped", "absent"]


@dataclass(frozen=True)
class FixedSource:
    """A prepared selection, never a callback into lifecycle management."""

    connector: EnvironmentConnector

    @property
    def provider_key(self) -> str:
        return self.connector.provider_key

    @property
    def environment_id(self) -> str:
        return self.connector.environment_id

    @property
    def descriptor(self) -> EnvironmentDescriptor:
        return self.connector.descriptor

    @property
    def state(self) -> EnvironmentState | None:
        return self.connector.state

    async def ensure_ready(self) -> EnvironmentConnector:
        # Harness opens and checks a fresh execution. A missing/stopped target fails;
        # it cannot cause provisioning or retarget an already admitted execution.
        return self.connector


class ManagedTarget(Protocol):
    @property
    def state(self) -> EnvironmentState | None: ...
    def source(self) -> FixedSource: ...
    async def reconcile(self) -> TargetStatus: ...
    async def prepare(self) -> None: ...
    async def stop(self) -> None: ...
    async def destroy(self) -> None: ...


class LocalTarget:
    def __init__(self, target: TargetRecord):
        self.configuration = DirectLocalEnvironmentConfiguration.model_validate(
            target.definition["configuration"]
        )
        self.connector = DIRECT_LOCAL.execution_connector(
            self.configuration,
            environment_id=target.id,
            state=EnvironmentState.model_validate(target.provider_state)
            if target.provider_state is not None
            else None,
        )

    @property
    def state(self) -> None:
        return None

    def source(self) -> FixedSource:
        return FixedSource(self.connector)

    async def reconcile(self) -> TargetStatus:
        return "stopped"  # No daemon or disposable backing resource.

    async def prepare(self) -> None:
        async with await self.connector.open():
            pass

    async def stop(self) -> None:
        pass

    async def destroy(self) -> None:
        # The operator owns this directory, not the target association.
        pass


class DockerTarget:
    """Native Docker management, serialized and persisted by EnvironmentManager."""

    def __init__(self, target: TargetRecord):
        configuration = target.definition["configuration"]
        if not isinstance(configuration, dict):
            raise ClawError("target_invalid", "Docker target configuration must be an object")
        recipe = dict(configuration)
        # This is now Host policy, retained in the generation for 0.8 compatibility.
        if recipe.pop("pull_policy", "if_missing") != "if_missing":
            raise ClawError("target_invalid", "Unsupported Docker image pull policy")
        self.configuration = DockerEnvironmentConfiguration.model_validate(recipe)
        self.connection = DockerConnectionConfiguration()
        self.environment_id = target.id
        self.fingerprint = hashlib.sha256(
            json.dumps(self.configuration.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        self._state = (
            EnvironmentState.model_validate(target.provider_state)
            if target.provider_state is not None
            else None
        )
        remembered = decode_target_state(
            "docker", self._state, DockerProviderStateData, fingerprint=self.fingerprint
        )
        if remembered is not None and remembered.environment_id != target.id:
            raise ClawError("target_conflict", "Docker state belongs to another target")
        self.container_id = remembered.container_id if remembered else None

    @property
    def state(self) -> EnvironmentState | None:
        return self._state.model_copy(deep=True) if self._state is not None else None

    @property
    def labels(self) -> dict[str, str]:
        return {"a13n.environment": self.environment_id, "a13n.configuration": self.fingerprint}

    def source(self) -> FixedSource:
        return FixedSource(
            DOCKER.execution_connector(
                self.configuration,
                configuration=self.connection,
                environment_id=self.environment_id,
                state=self.state,
            )
        )

    def _remember(self, container: Container) -> None:
        if not isinstance(container.id, str) or not container.id:
            raise ClawError("target_unconfirmed", "Docker did not return a container identity")
        self.container_id = container.id
        self._state = EnvironmentState(
            provider_key="docker",
            state_version="1",
            state=DockerProviderStateData(
                environment_id=self.environment_id,
                container_id=container.id,
                configuration_fingerprint=self.fingerprint,
            ).model_dump(mode="json"),
        )

    def _lookup(self, client: docker.DockerClient) -> Container | None:
        selectors = ["a13n-" + self.environment_id]
        if self.container_id is not None:
            selectors.insert(0, self.container_id)
        for selector in selectors:
            try:
                container = client.containers.get(selector)
            except NotFound:
                continue
            if any(container.labels.get(key) != value for key, value in self.labels.items()):
                raise ClawError("target_conflict", "Docker target ownership does not match")
            self._remember(container)
            return container
        return None

    def _create(self, client: docker.DockerClient) -> Container:
        config = self.configuration
        try:
            image = client.images.get(config.image)
        except ImageNotFound:
            image = client.images.pull(config.image)
        if not isinstance(image.id, str) or not image.id:
            raise ClawError("target_unconfirmed", "Docker did not return an image identity")
        return client.containers.create(
            image.id,
            name="a13n-" + self.environment_id,
            entrypoint=[config.python, "-I", "-c"],
            command=["import signal; signal.pause()"],
            init=True,
            detach=True,
            user=config.user,
            working_dir="/workspace",
            environment=config.environment,
            network_mode="none" if config.disable_network else "bridge",
            mounts=[
                Mount(str(m.target), m.source, type="bind", read_only=m.read_only)
                for m in config.mounts
            ],
            labels=self.labels,
            nano_cpus=int(config.cpus * 1_000_000_000) if config.cpus is not None else None,
            mem_limit=int(config.memory_gb * 1_000_000_000)
            if config.memory_gb is not None
            else None,
            pids_limit=config.pids_limit,
        )

    def _execute(self, container: Container, argv: list[str]) -> None:
        result = container.exec_run(argv, user=self.configuration.user or "", workdir="/workspace")
        if result.exit_code != 0:
            raise ClawError("target_unavailable", "Docker target initialization failed")

    def _operate(self, action: str) -> TargetStatus:
        config = self.configuration
        with closing(
            docker.DockerClient(
                base_url=self.connection.docker_host, timeout=config.request_timeout_seconds
            )
        ) as client:
            container = self._lookup(client)
            if action == "prepare":
                created = container is None
                if container is None:
                    container = self._create(client)
                    self._remember(container)
                if container.status != "running":
                    container.start()
                if created:
                    self._execute(
                        container,
                        [
                            config.python,
                            "-I",
                            "-c",
                            "import os; os.makedirs('/workspace',exist_ok=True); "
                            "os.makedirs('/tmp/a13n',exist_ok=True)",
                        ],
                    )
                    if config.init_script:
                        self._execute(container, [config.shell, "-c", config.init_script])
                    self._execute(
                        container,
                        [config.python, "-I", "-c", "open('/tmp/a13n/initialized','w').close()"],
                    )
                # An interrupted initialization is never silently replayed.
                self._execute(
                    container,
                    [
                        config.python,
                        "-I",
                        "-c",
                        "import os; assert os.path.isfile('/tmp/a13n/initialized')",
                    ],
                )
                container.reload()
                if container.status != "running":
                    raise ClawError("target_unavailable", "Docker target is not running")
            elif action == "stop" and container is not None:
                if container.status == "running":
                    container.stop(timeout=config.stop_grace_seconds)
                container.reload()
            elif action == "remove" and container is not None:
                container.remove(force=True)
                container = self._lookup(client)
                if container is not None:
                    raise ClawError("target_unconfirmed", "Docker removal was not confirmed")
            if container is None:
                self.container_id = None
                self._state = None
                return "absent"
            return "running" if container.status == "running" else "stopped"

    async def reconcile(self) -> TargetStatus:
        return await settle_on_cancel(asyncio.to_thread(self._operate, "inspect"))

    async def prepare(self) -> None:
        await settle_on_cancel(asyncio.to_thread(self._operate, "prepare"))
        async with await self.source().connector.open():
            pass

    async def stop(self) -> None:
        await settle_on_cancel(asyncio.to_thread(self._operate, "stop"))

    async def destroy(self) -> None:
        await settle_on_cancel(asyncio.to_thread(self._operate, "remove"))


async def create_target(target: TargetRecord) -> ManagedTarget:
    return LocalTarget(target) if target.definition["kind"] == "local" else DockerTarget(target)
