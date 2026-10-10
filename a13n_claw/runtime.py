"""Resolve captured application definitions into fresh published Harness objects."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from a13n_harness import HarnessBuilder, RunBindings
from a13n_harness.capabilities import (
    FileSkillSource,
    SkillManager,
    SkillsCapability,
    UserInteractionCapability,
)
from a13n_harness.context import AgentContext
from a13n_harness.environment import (
    FILE_READ_ACTIONS,
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    EnvironmentMount,
    EnvironmentPermissionSet,
)
from a13n_harness.mcp import ContextualMCP
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
from a13n_harness.tools.permissions import ToolPermissionsCapability
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model

from a13n_claw.assets import asset_capability
from a13n_claw.collaboration import collaboration_capability, messaging_capability
from a13n_claw.coordinator import CheckpointCapability, RunRuntime
from a13n_claw.delegation import delegation_capability
from a13n_claw.domain import (
    ClawError,
    MCPDefinition,
    ModelDefinition,
    ProfileDefinition,
    RunRecord,
    SkillDefinition,
)
from a13n_claw.environments import EnvironmentManager, local_configuration
from a13n_claw.storage import Store

ModelFactory = Callable[[ModelDefinition, str], AbstractAsyncContextManager[Model]]


@asynccontextmanager
async def provider_model(definition: ModelDefinition, credential: str) -> AsyncIterator[Model]:
    if definition.provider == "openai":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        async with OpenAIProvider(api_key=credential, base_url=definition.base_url) as provider:
            yield OpenAIChatModel(definition.model_name, provider=provider)
    elif definition.provider == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        async with AnthropicProvider(api_key=credential, base_url=definition.base_url) as provider:
            yield AnthropicModel(definition.model_name, provider=provider)
    else:
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        async with GoogleProvider(api_key=credential, base_url=definition.base_url) as provider:
            yield GoogleModel(definition.model_name, provider=provider)


def write_skills(root: Path, run: RunRecord) -> None:
    """Materialize retained content, never rediscover mutable installation directories."""
    for resource in run.composition.skills:
        skill = SkillDefinition.model_validate(resource.content)
        directory = root / skill.name
        directory.mkdir()
        # JSON string quoting is also valid YAML and cannot inject extra frontmatter keys.
        document = (
            f"---\nname: {json.dumps(skill.name)}\n"
            f"description: {json.dumps(skill.description)}\n---\n\n{skill.instructions}\n"
        )
        (directory / "SKILL.md").write_text(document, encoding="utf-8")
        for name, content in skill.files.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")


class Runtime:
    def __init__(
        self,
        store: Store,
        data_root: Path,
        *,
        environments: EnvironmentManager | None = None,
        models: ModelFactory = provider_model,
    ):
        self.store = store
        self.data_root = data_root
        self.environments = environments or EnvironmentManager(store)
        self.models = models

    @asynccontextmanager
    async def __call__(
        self,
        run: RunRecord,
        checkpoint: CheckpointCapability,
    ) -> AsyncIterator[RunRuntime]:
        assert run.owner is not None
        role = await asyncio.to_thread(self.store.coordination.capability_role, run.id, run.owner)
        profile = ProfileDefinition.model_validate(run.composition.profile.content)
        definition = ModelDefinition.model_validate(run.composition.model.content)
        servers = [
            (resource.id, MCPDefinition.model_validate(resource.content))
            for resource in run.composition.mcp_servers
        ]
        references = {definition.credential_env} | {
            ref for _, server in servers for ref in server.header_env.values()
        }
        credentials = {
            name: await asyncio.to_thread(self.store.resolve_credential, name)
            for name in references
        }

        async def authorize() -> None:
            await checkpoint.authorize()
            for name, value in credentials.items():
                current = await asyncio.to_thread(self.store.resolve_credential, name)
                if current != value:
                    raise ClawError(
                        "credential_changed", "Credential changed during execution; start new work"
                    )

        async with AsyncExitStack() as stack:
            model = await stack.enter_async_context(
                self.models(definition, credentials[definition.credential_env])
            )
            workspace = await stack.enter_async_context(self.environments.acquire(run))
            mounts = {"workspace": workspace}
            capabilities: list[AbstractCapability[AgentContext]] = [
                CheckpointCapability(checkpoint.save, authorize),
                DynamicEnvironmentCapability(
                    DynamicEnvironmentConfiguration(computer_enabled=False)
                ),
                ToolPermissionsCapability(profile.permissions),
                UserInteractionCapability(),
                asset_capability(self.store, run),
            ]
            if role is not None:
                capabilities.append(collaboration_capability(self.store, run, main=role == "main"))
            if role == "main":
                capabilities.append(messaging_capability(self.store, run))
            if run.composition.children:
                capabilities.append(delegation_capability(self.store, run))
            if run.composition.skills:
                temporary = stack.enter_context(
                    TemporaryDirectory(prefix="claw-skills-", dir=self.data_root)
                )
                await asyncio.to_thread(write_skills, Path(temporary), run)
                environment = await DIRECT_LOCAL.create(local_configuration(temporary, False))
                stack.push_async_callback(environment.close)
                mounts["skills"] = EnvironmentMount(
                    environment=environment,
                    permission_ceiling=EnvironmentPermissionSet(operations=FILE_READ_ACTIONS),
                    mount_path="/claw-skills",
                    provider_root="/",
                    working_directory="/claw-skills",
                )
                capabilities.append(
                    SkillsCapability(
                        SkillManager(
                            [
                                FileSkillSource("captured", ("/claw-skills",)),
                            ]
                        )
                    )
                )
            for resource_id, server in servers:
                headers = {
                    name: credentials[reference] for name, reference in server.header_env.items()
                }

                async def headers_factory(context: AgentContext, values=headers):
                    del context
                    await authorize()
                    return values

                capabilities.append(
                    ContextualMCP(
                        server.url,
                        id=f"claw.mcp.{resource_id}",
                        headers_factory=headers_factory,
                        allowed_tools=server.allowed_tools,
                        description=server.description,
                    )
                )
            executable = HarnessBuilder(
                instrumentation=None,
                configured_plugins_enabled=False,
            ).build(
                profile.agent,
                output_type=str,
                model=model,
                definition_id=f"{run.composition.profile.id}@{run.composition.profile.version}",
                capabilities=capabilities,
            )
            yield RunRuntime(executable, RunBindings.embedded(), mounts)
