"""Host-owned target lifecycle; fresh Harness operational access for every Run."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Literal

from a13n_harness.environment import (
    FILE_ACTIONS,
    FILE_READ_ACTIONS,
    Environment,
    EnvironmentAction,
    EnvironmentMount,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from a13n_harness.environment.advanced import BoundEnvironment
from a13n_harness.environment.coordinator import create_environment_runtime
from a13n_harness.identity import AgentIdentityRef, AgentInstanceContext
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
    DirectLocalShellProfile,
)
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
from a13n_harness.providers.environment.docker.configuration import (
    DockerEnvironmentConfiguration,
    DockerMountConfiguration,
)
from a13n_harness.providers.environment.docker.provider import DOCKER

from a13n_claw.async_utils import settle_on_cancel
from a13n_claw.domain import ClawError, EnvironmentDefinition, RunRecord, TargetRecord, new_id
from a13n_claw.storage import Store

logger = logging.getLogger("a13n_claw.environments")
AdapterFactory = Callable[[TargetRecord], Awaitable[Environment]]


def local_configuration(workspace: str, shell: bool) -> DirectLocalEnvironmentConfiguration:
    profiles: tuple[DirectLocalShellProfile, ...] = ()
    if shell:
        executable = (
            shutil.which("pwsh") or shutil.which("powershell")
            if os.name == "nt"
            else shutil.which("sh")
        )
        if executable is None:
            raise ClawError("shell_unavailable", "No supported local shell is installed")
        profiles = (
            DirectLocalShellProfile(
                profile_id="default",
                executable=Path(executable).resolve(),
                dialect="powershell" if os.name == "nt" else "posix",
            ),
        )
    return DirectLocalEnvironmentConfiguration(
        root=DirectLocalRootConfiguration(path=Path(workspace)),
        shell_profiles=profiles,
        inherit_environment=False,
    )


def target_definition(run: RunRecord) -> dict:
    selected = EnvironmentDefinition.model_validate(run.composition.environment.content)
    if selected.kind == "local":
        recipe = local_configuration(run.composition.workspace, selected.shell)
    else:
        assert selected.image is not None
        recipe = DockerEnvironmentConfiguration(
            image=selected.image,
            user=selected.user,
            mounts=(
                DockerMountConfiguration(
                    source=run.composition.workspace,
                    target=PurePosixPath("/claw-workspace"),
                    read_only=selected.files != "write",
                ),
            ),
        )
    return {"kind": selected.kind, "configuration": recipe.model_dump(mode="json")}


def permissions(selected: EnvironmentDefinition) -> EnvironmentPermissionSet:
    operations = set(
        FILE_ACTIONS
        if selected.files == "write"
        else FILE_READ_ACTIONS
        if selected.files == "read"
        else ()
    )
    if selected.shell:
        operations.update(
            action
            for action in EnvironmentAction
            if action.value.startswith(
                ("environment.shell.", "environment.process.", "environment.output.")
            )
        )
    return EnvironmentPermissionSet(operations=frozenset(operations))


async def create_adapter(target: TargetRecord) -> Environment:
    """Only the installed provider catalog can construct operational access."""
    if target.definition["kind"] == "local":
        environment = await DIRECT_LOCAL.create(
            DirectLocalEnvironmentConfiguration.model_validate(target.definition["configuration"]),
            environment_id=target.id,
        )
    else:
        environment = await DOCKER.create(
            DockerEnvironmentConfiguration.model_validate(target.definition["configuration"]),
            environment_id=target.id,
            state=EnvironmentState.model_validate(target.provider_state)
            if target.provider_state
            else None,
        )
    # Only the manager can replace backing targets, never a tool in an active Run.
    environment.recover_on_unavailable = False
    return environment


class EnvironmentManager:
    def __init__(self, store: Store, factory: AdapterFactory = create_adapter):
        self.store = store
        self.factory = factory
        self.locks: dict[str, asyncio.Lock] = {}
        self.users: dict[str, int] = {}

    def _lock(self, target_id: str) -> asyncio.Lock:
        return self.locks.setdefault(target_id, asyncio.Lock())

    async def _observe(
        self,
        target: TargetRecord,
        environment: Environment,
        status: str,
        error: str | None = None,
    ) -> TargetRecord:
        state = environment.dump_state()
        return await settle_on_cancel(
            asyncio.to_thread(
                self.store.target_observed,
                target.id,
                target.operation_id,
                status=status,
                provider_state=state.model_dump(mode="json") if state else None,
                error=error,
            )
        )

    @asynccontextmanager
    async def acquire(self, run: RunRecord) -> AsyncIterator[EnvironmentMount]:
        if run.owner is None:
            raise ClawError("stale_owner", "Environment preparation requires an execution owner")
        selected = EnvironmentDefinition.model_validate(run.composition.environment.content)
        target = await asyncio.to_thread(
            self.store.target_for, run.id, run.owner, target_definition(run)
        )
        environment: Environment | None = None
        acquired = False
        try:
            async with self._lock(target.id):
                await asyncio.to_thread(self.store.authorize_execution, run.id, run.owner)
                target = await settle_on_cancel(
                    asyncio.to_thread(self.store.target_operation, target.id, "prepare")
                )
                try:
                    environment = await self.factory(target)
                    # Inspect an interrupted or uncertain preparation before any mutation.
                    await environment.reconcile()
                    await settle_on_cancel(environment.prepare())
                    await self._observe(target, environment, "ready")
                except BaseException:
                    if environment is not None:
                        await self._observe(
                            target, environment, "unavailable", "preparation_failed"
                        )
                    else:
                        await settle_on_cancel(
                            asyncio.to_thread(
                                self.store.target_observed,
                                target.id,
                                target.operation_id,
                                status="unavailable",
                                provider_state=target.provider_state,
                                error="provider_unavailable",
                            )
                        )
                    raise
                self.users[target.id] = self.users.get(target.id, 0) + 1
                acquired = True
            yield EnvironmentMount(
                environment=environment,
                permission_ceiling=permissions(selected),
                mount_path="/workspace",
                working_directory="/workspace",
                provider_root="/" if selected.kind == "local" else "/claw-workspace",
            )
        finally:
            if environment is not None:
                await settle_on_cancel(
                    self._release(target, environment, selected.retention, acquired)
                )

    @asynccontextmanager
    async def files(self, actor_id: str, run_id: str) -> AsyncIterator[BoundEnvironment]:
        """Console reads use the selected provider, never a server-path shortcut."""
        run = await asyncio.to_thread(self.store.run, actor_id, run_id)
        definition = target_definition(run)
        target = await asyncio.to_thread(self.store.files_target, actor_id, run_id, definition)
        selected = EnvironmentDefinition.model_validate(run.composition.environment.content)
        environment: Environment | None = None
        acquired = False
        try:
            async with self._lock(target.id):
                target = await asyncio.to_thread(
                    self.store.files_target, actor_id, run_id, definition
                )
                if target.status != "ready":
                    raise ClawError(
                        "target_unavailable", "Prepare the selected target before browsing files"
                    )
                environment = await self.factory(target)
                observed = await environment.reconcile()
                if selected.kind == "local":
                    # Local has no durable daemon; every operational adapter is fresh.
                    await settle_on_cancel(environment.prepare())
                elif observed != "running":
                    await self._observe(
                        target, environment, "unavailable", "files_target_unavailable"
                    )
                    raise ClawError("target_unavailable", "Selected target is not running")
                self.users[target.id] = self.users.get(target.id, 0) + 1
                acquired = True
            runtime = create_environment_runtime(
                mounts={
                    "workspace": EnvironmentMount(
                        environment=environment,
                        permission_ceiling=EnvironmentPermissionSet(operations=FILE_READ_ACTIONS),
                        mount_path="/workspace",
                        working_directory="/workspace",
                        provider_root="/" if selected.kind == "local" else "/claw-workspace",
                    )
                },
                default_mount="workspace",
            )
            access_id = new_id("file-access")
            async with runtime.bind(
                thread_id=run.thread_id,
                run_id=access_id,
                instance=AgentInstanceContext(
                    identity=AgentIdentityRef(issuer="claw", subject=actor_id),
                    agent_instance_id=access_id,
                    actor=actor_id,
                ),
                host_refs={},
            ) as bound:
                await asyncio.to_thread(self.store.files_target, actor_id, run_id, definition)
                yield bound
        finally:
            if environment is not None:
                await settle_on_cancel(
                    self._release(target, environment, selected.retention, acquired)
                )

    async def _release(
        self,
        target: TargetRecord,
        environment: Environment,
        retention: str,
        acquired: bool,
    ) -> None:
        async with self._lock(target.id):
            try:
                await environment.close()
            except Exception:
                await self._observe(target, environment, "unavailable", "access_cleanup_failed")
                logger.warning("Target access cleanup failed: %s", target.id)
            finally:
                if acquired:
                    self.users[target.id] -= 1
            if acquired and not self.users.get(target.id) and retention == "stop":
                try:
                    await self._manage_locked(target, "stop")
                except Exception:
                    # Target cleanup cannot change an already committed execution outcome.
                    logger.warning("Target idle stop failed: %s", target.id)

    async def manage(
        self,
        actor_id: str,
        target_id: str,
        action: Literal["inspect", "prepare", "stop", "remove"],
    ) -> TargetRecord:
        async with self._lock(target_id):
            target = await asyncio.to_thread(self.store.target, actor_id, target_id, manage=True)
            if self.users.get(target_id):
                raise ClawError(
                    "target_busy", "Release active target use before managing its lifecycle"
                )
            return await self._manage_locked(target, action)

    async def _manage_locked(
        self,
        target: TargetRecord,
        action: Literal["inspect", "prepare", "stop", "remove"],
    ) -> TargetRecord:
        target = await settle_on_cancel(
            asyncio.to_thread(self.store.target_operation, target.id, action)
        )
        environment: Environment | None = None
        try:
            environment = await self.factory(target)
            observed = await environment.reconcile()
            status = {"running": "ready", "stopped": "stopped", "absent": "removed"}[observed]
            if action == "prepare":
                await settle_on_cancel(environment.prepare())
                status = "ready"
            elif action == "stop":
                await settle_on_cancel(environment.stop())
                observed = await environment.reconcile()
                if observed == "running":
                    raise ClawError("target_unconfirmed", "Provider did not confirm target stop")
                status = "stopped" if observed == "stopped" else "removed"
            elif action == "remove":
                await settle_on_cancel(environment.destroy())
                status = "removed"
            return await self._observe(target, environment, status)
        except BaseException:
            if environment is not None:
                await self._observe(target, environment, "unavailable", "management_failed")
            else:
                await settle_on_cancel(
                    asyncio.to_thread(
                        self.store.target_observed,
                        target.id,
                        target.operation_id,
                        status="unavailable",
                        provider_state=target.provider_state,
                        error="provider_unavailable",
                    )
                )
            raise
        finally:
            if environment is not None:
                await settle_on_cancel(environment.close())
