"""Claw-owned, host-restricted connector credentials for native Project Sessions."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from a13n_envd_client.eip.v1 import EgressMode, PublicDestinations
from a13n_service.app_sdk import egress as sdk_egress
from a13n_service.app_sdk import threads as sdk_threads
from a13n_service.app_sdk.threads import SessionConfiguration
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import invalid
from a13n_service.resources.environment_instances.session_configuration import EgressConfiguration
from a13n_service.tenancy.tables import WorkspaceRow
from sqlalchemy import select

from . import connectors
from .feishu import LarkAdapter, LarkSettings
from .inbox import new_id
from .models import APP_ID, Claw, Connector, ConnectorEgress, claw_context

GITHUB_HOSTS = ("api.github.com", "github.com", "uploads.github.com")
LARK_HOSTS = {
    "https://open.feishu.cn": "open.feishu.cn",
    "https://open.larksuite.com": "open.larksuite.com",
}


def github_hosts(domain: str | None) -> tuple[str, ...]:
    if domain is None or domain == "api.github.com":
        return GITHUB_HOSTS
    return (domain,)


def target(kind: str, config: dict) -> str | None:
    if kind == "lark":
        return config.get("domain")
    host = urlsplit(config.get("api_url", "https://api.github.com")).hostname
    if not host:
        raise ValueError("GitHub API URL must have a host")
    return None if host == "api.github.com" else host


@dataclass(frozen=True, slots=True)
class Binding:
    kind: Literal["github", "lark"]
    credential_id: str
    domain: str | None = None


def session_configuration(
    bindings: tuple[Binding, ...], base: SessionConfiguration | None = None
) -> SessionConfiguration:
    """Apply references without widening the selected Agent's policy."""

    base = base or SessionConfiguration()
    if base.egress_mode is None and base.egress is None:
        destinations = PublicDestinations(mode="public")
    elif base.egress_mode == "controlled" and base.egress is not None:
        destinations = base.egress.destinations
    else:
        raise ValueError(
            "Credential injection requires controlled egress with a destination policy"
        )
    secrets = list(base.credential_references)
    additions = []
    seen: set[str] = set()
    for binding in bindings:
        if binding.kind in seen:
            raise ValueError("Only one primary connector per platform is supported")
        seen.add(binding.kind)
        if binding.kind == "github":
            env, hosts = "GH_TOKEN", github_hosts(binding.domain)
        else:
            if binding.domain not in LARK_HOSTS:
                raise ValueError("Unsupported Lark region")
            env, hosts = "LARKSUITE_CLI_TENANT_ACCESS_TOKEN", (LARK_HOSTS[binding.domain],)
        additions.append(
            {"env": env, "credential_id": binding.credential_id, "inject_hosts": hosts}
        )
    refs = {ref.env: ref for ref in secrets}
    for ref in additions:
        previous = refs.get(ref["env"])
        if previous is not None and previous.credential_id != ref["credential_id"]:
            raise ValueError("Credential environment name is already in use")
        refs[ref["env"]] = ref
    policy = EgressConfiguration.model_validate(
        {"destinations": destinations, "secrets": tuple(refs.values())}
    )
    return base.model_copy(update={"egress_mode": EgressMode.CONTROLLED, "egress": policy})


def token_refresh_delay(lifetime: int) -> int:
    """Refresh after at most 75 minutes, leaving time before token expiry."""
    if lifetime <= 0:
        raise ValueError("Token lifetime must be positive")
    return max(1, min(4500, lifetime - 900 if lifetime > 1800 else lifetime // 2))


async def commit_generation(session, runtime, claw, generation, value: str, expires_at) -> None:
    """Create or rotate and bind one generation inside the caller's locked transaction.

    The caller must acquire App admission before the Claw lock and fence the
    connector generation after any external token exchange. The supplied value
    must never be persisted in Claw state.
    """

    if generation.state != "preparing":
        raise ValueError("Connector generation is no longer current")
    if generation.kind == "github":
        env, hosts = "GH_TOKEN", github_hosts(generation.domain)
    elif generation.kind == "lark" and generation.domain in LARK_HOSTS:
        env, hosts = "LARKSUITE_CLI_TENANT_ACCESS_TOKEN", (LARK_HOSTS[generation.domain],)
    else:
        raise ValueError("Unsupported connector platform or region")
    creating = generation.credential_id is None
    if creating:
        credential = await sdk_egress.create_app_credential_in_transaction(
            session,
            runtime,
            claw.workspace_id,
            app_id=APP_ID,
            actor_id=claw.service_account_id,
            name=f"Claw connector {generation.id}",
            allowed_hosts=hosts,
            value=value,
        )
        generation.credential_id = credential.id
    else:
        await sdk_egress.rotate_app_credential_in_transaction(
            session,
            runtime,
            claw.workspace_id,
            app_id=APP_ID,
            actor_id=claw.service_account_id,
            credential_id=generation.credential_id,
            value=value,
        )
    # Rotation changes only the credential value. Existing references already
    # point to this ID, including a Main created after the credential became
    # ready; Main allocation captures ready or refreshing generations while
    # holding the same Claw lock. Repatching would require an idle subtree.
    if creating and claw.main_thread_id is not None:
        await sdk_threads.update_app_egress_secrets(
            session,
            runtime,
            claw.workspace_id,
            app_id=APP_ID,
            principal_id=claw.service_account_id,
            root_thread_id=claw.main_thread_id,
            set_secrets=(
                sdk_egress.SecretReference(
                    env=env, credential_id=generation.credential_id, inject_hosts=hosts
                ),
            ),
            initial_destinations=PublicDestinations(mode="public"),
        )
    generation.token_expires_at = expires_at
    generation.state = "ready"
    generation.failure = None


async def revoke_generation(session, runtime, claw, generation) -> None:
    """Remove the owned reference and revoke its value in one transaction."""

    if generation.credential_id is None:
        generation.state = "revoked"
        return
    if generation.kind == "github":
        env = "GH_TOKEN"
    elif generation.kind == "lark":
        env = "LARKSUITE_CLI_TENANT_ACCESS_TOKEN"
    else:
        raise ValueError("Unsupported connector platform")
    # A delayed revocation must not remove a newer generation's reference.
    replacement = await session.scalar(
        select(ConnectorEgress.id).where(
            ConnectorEgress.claw_id == claw.id,
            ConnectorEgress.kind == generation.kind,
            ConnectorEgress.id != generation.id,
            ConnectorEgress.state.in_(("ready", "preparing")),
            ConnectorEgress.credential_id.is_not(None),
        )
    )
    if claw.main_thread_id is not None and replacement is None:
        await sdk_threads.update_app_egress_secrets(
            session,
            runtime,
            claw.workspace_id,
            app_id=APP_ID,
            principal_id=claw.service_account_id,
            root_thread_id=claw.main_thread_id,
            remove_envs=(env,),
        )
    if generation.state != "unbinding":
        await sdk_egress.revoke_app_credential_in_transaction(
            session,
            runtime,
            claw.workspace_id,
            app_id=APP_ID,
            actor_id=claw.service_account_id,
            credential_id=generation.credential_id,
        )
    generation.state = "revoked"
    generation.failure = None


async def invalidate_generations(session, connector_id: str) -> None:
    """Fence in-flight work and retain known credentials for durable revocation.

    Caller holds the Claw lock. Credentials are not removed from storage until
    the Service revocation transaction succeeds.
    """

    rows = await session.scalars(
        select(ConnectorEgress)
        .where(ConnectorEgress.connector_id == connector_id, ConnectorEgress.state != "revoked")
        .with_for_update()
    )
    for row in rows:
        if row.state != "unbinding":
            row.state = "revoke_pending" if row.credential_id is not None else "revoked"
        row.failure = None


async def enqueue_generation(session, claw, connector) -> None:
    """Queue a fresh generation only for a selected enabled connector."""

    if not connector.enabled or not connector.egress_primary or connector.credential is None:
        return
    domain = target(connector.kind, connector.config)
    session.add(
        ConnectorEgress(
            id=new_id("ceg"),
            workspace_id=claw.workspace_id,
            claw_id=claw.id,
            connector_id=connector.id,
            kind=connector.kind,
            domain=domain,
            actor_id=claw.service_account_id,
            state="pending",
        )
    )


async def process_generation(runtime, workspace_id: str, claw_id: str, generation_id: str) -> None:
    """Reconcile one generation and retain a non-secret failure diagnostic."""

    try:
        await _process_generation(runtime, workspace_id, claw_id, generation_id)
    except Exception:
        async with transaction(runtime.storage) as session:
            row = await session.get(ConnectorEgress, generation_id, with_for_update=True)
            if row is not None and row.claw_id == claw_id and row.state != "revoked":
                row.failure = "credential_sync_failed"


async def _process_generation(runtime, workspace_id: str, claw_id: str, generation_id: str) -> None:
    """Reconcile one generation with a fenced token exchange outside transactions."""
    async with short_session(runtime.storage) as session:
        claw = await session.get(Claw, claw_id)
        if claw is None or claw.workspace_id != workspace_id:
            return
        account_id = claw.service_account_id
    now = datetime.now(UTC)
    async with transaction(runtime.storage) as session:
        await sdk_threads.prepare_admission(
            session,
            runtime,
            workspace_id,
            account_id,
            app_id=APP_ID,
            app_context=claw_context(claw_id),
        )
        claw = await session.scalar(
            select(Claw)
            .where(Claw.id == claw_id, Claw.workspace_id == workspace_id)
            .with_for_update()
        )
        row = await session.get(ConnectorEgress, generation_id, with_for_update=True)
        if claw is None or row is None or row.claw_id != claw_id:
            return
        if row.state == "revoke_pending":
            # Revoke the Service credential even if a busy or closed Thread
            # prevents removing its reference. Cleanup can then retry safely.

            if row.credential_id is not None:
                await sdk_egress.revoke_app_credential_in_transaction(
                    session,
                    runtime,
                    claw.workspace_id,
                    app_id=APP_ID,
                    actor_id=claw.service_account_id,
                    credential_id=row.credential_id,
                )
                row.state = "unbinding"
            else:
                row.state = "revoked"
            row.failure = None
            return
        if row.state == "unbinding":
            await revoke_generation(session, runtime, claw, row)
            return
        if row.state == "ready" and (row.refresh_at is None or row.refresh_at > now):
            return
        if (
            row.state == "preparing"
            and row.attempt_started_at
            and row.attempt_started_at > now - timedelta(seconds=45)
        ):
            return
        if row.state not in ("pending", "preparing", "ready"):
            return
        previous = await session.scalar(
            select(ConnectorEgress.id).where(
                ConnectorEgress.claw_id == claw.id,
                ConnectorEgress.kind == row.kind,
                ConnectorEgress.state.in_(("revoke_pending", "unbinding")),
                ConnectorEgress.id != row.id,
            )
        )
        if previous:
            return
        connector = await session.get(Connector, row.connector_id)
        if (
            claw.archived_at is not None
            or connector is None
            or not connector.enabled
            or not connector.egress_primary
            or connector.credential is None
        ):
            row.state = "revoke_pending" if row.credential_id else "revoked"
            return
        workspace_org = await session.get_one(WorkspaceRow, workspace_id)
        secret = connectors.credential(runtime, workspace_org.organization_id, connector)
        if secret is None:
            row.state = "revoke_pending" if row.credential_id else "revoked"
            return
        config = dict(connector.config)
        encrypted = dict(connector.credential)
        row.state = "preparing"
        row.attempt_started_at = now
        kind = row.kind
    try:
        expires_at = None
        refresh_at = None
        value = secret
        if kind == "lark":
            adapter = LarkAdapter(generation_id, LarkSettings.model_validate(config), secret)
            try:
                async with asyncio.timeout(20):
                    value, lifetime = await adapter.exchange_token()
            finally:
                await adapter.close()
            acquired = datetime.now(UTC)
            expires_at = acquired + timedelta(seconds=lifetime)
            refresh_at = acquired + timedelta(seconds=token_refresh_delay(lifetime))
        async with transaction(runtime.storage) as session:
            await sdk_threads.prepare_admission(
                session,
                runtime,
                workspace_id,
                account_id,
                app_id=APP_ID,
                app_context=claw_context(claw_id),
            )
            claw = await session.scalar(
                select(Claw)
                .where(Claw.id == claw_id, Claw.workspace_id == workspace_id)
                .with_for_update()
            )
            row = await session.get(ConnectorEgress, generation_id, with_for_update=True)
            if (
                claw is None
                or row is None
                or row.state != "preparing"
                or row.attempt_started_at != now
            ):
                return
            connector = await session.get(Connector, row.connector_id)
            if (
                claw.archived_at is not None
                or connector is None
                or not connector.enabled
                or not connector.egress_primary
                or connector.credential != encrypted
                or target(connector.kind, connector.config) != target(kind, config)
            ):
                row.state = "revoke_pending" if row.credential_id else "revoked"
                return
            if connector.config.get("app_id") != config.get("app_id"):
                row.state = "ready" if row.credential_id else "pending"
                row.refresh_at = datetime.now(UTC)
                row.attempt_started_at = None
                return
            await commit_generation(session, runtime, claw, row, value, expires_at)
            row.refresh_at = refresh_at
    except Exception:
        # Do not record provider details or credentials. Retry after the lease.
        async with transaction(runtime.storage) as session:
            row = await session.get(ConnectorEgress, generation_id, with_for_update=True)
            if row is not None and row.state == "preparing" and row.attempt_started_at == now:
                row.failure = "credential_sync_failed"


async def initial_configuration(session, claw, base: SessionConfiguration | None = None):
    """Select ready owned references for a newly created Project mount."""

    rows = await session.scalars(
        select(ConnectorEgress).where(
            ConnectorEgress.claw_id == claw.id,
            ConnectorEgress.state.in_(("ready", "preparing")),
            ConnectorEgress.credential_id.is_not(None),
        )
    )
    bindings: list[Binding] = []
    for row in rows:
        if row.kind not in ("github", "lark") or row.credential_id is None:
            raise ValueError("Invalid connector credential generation")
        bindings.append(Binding(row.kind, row.credential_id, row.domain))
    if not bindings:
        return None
    try:
        return session_configuration(tuple(bindings), base)
    except ValueError as error:
        raise invalid("session_configuration", "connector_egress_policy_conflict") from error


async def invalidate_claw(session, claw_id: str) -> None:

    ids = await session.scalars(select(Connector.id).where(Connector.claw_id == claw_id))
    for connector_id in ids:
        await invalidate_generations(session, connector_id)


async def revoke_for_rebind(session, runtime, claw) -> None:

    await invalidate_claw(session, claw.id)
    rows = await session.scalars(
        select(ConnectorEgress)
        .where(
            ConnectorEgress.claw_id == claw.id,
            ConnectorEgress.state.in_(("revoke_pending", "unbinding")),
        )
        .with_for_update()
    )
    for row in rows:
        await revoke_generation(session, runtime, claw, row)
