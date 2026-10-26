"""Focused model and subscription-group editing through Host configuration."""

from typing import Literal

import yaml
from a13n_harness_ui.configuration.models import ModelResource, SubscriptionGroupResource
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput, ApiKeyStatus
from a13n_harness_ui.model_authoring import (
    ModelChoices,
    ModelOptions,
    ModelOptionsRequest,
    ModelRecipe,
    ModelRecipeRequest,
)
from a13n_harness_ui.model_catalog import ModelCatalogSnapshot
from fastapi import APIRouter

from a13n_claw.api.configuration import check_source
from a13n_claw.api.dependencies import Application
from a13n_claw.configuration.models import ProductModel

router = APIRouter()


class ResourceView[T](ProductModel):
    resource: T
    relative_path: str
    source_digest: str


class ModelInventory(ProductModel):
    models: tuple[ResourceView[ModelResource], ...]
    groups: tuple[ResourceView[SubscriptionGroupResource], ...]


class ResourceWrite[T](ProductModel):
    resource: T
    relative_path: str
    expected_digest: str | None


class ResourceDelete(ProductModel):
    relative_path: str
    expected_digest: str


class SourceResult(ProductModel):
    action: Literal["created", "updated", "deleted", "unchanged"]
    relative_path: str
    source_digest: str | None


@router.get("/models")
async def models(app: Application) -> ModelInventory:
    current = await app.host.current_configuration()
    if current is None:
        return ModelInventory(models=(), groups=())
    sources = {source.resource_id: source for source in current.sources}
    return ModelInventory(
        models=tuple(
            ResourceView[ModelResource](
                resource=model,
                relative_path=sources[model.id].relative_path,
                source_digest=sources[model.id].source_digest,
            )
            for model in current.models.values()
        ),
        groups=tuple(
            ResourceView[SubscriptionGroupResource](
                resource=group,
                relative_path=sources[group.id].relative_path,
                source_digest=sources[group.id].source_digest,
            )
            for group in current.subscription_groups.values()
        ),
    )


@router.get("/models/choices")
async def choices(app: Application) -> ModelChoices:
    return await app.host.model_choices()


@router.get("/models/catalog")
async def catalog(app: Application) -> ModelCatalogSnapshot:
    return await app.host.model_catalog()


@router.post("/models/options")
async def options(body: ModelOptionsRequest, app: Application) -> ModelOptions:
    return await app.host.model_options(body)


@router.post("/models/prepare")
async def prepare(body: ModelRecipeRequest, app: Application) -> ModelRecipe:
    return await app.host.prepare_model(body)


@router.put("/models")
async def write_model(body: ResourceWrite[ModelResource], app: Application) -> SourceResult:
    return await write_resource(body, app, "models")


@router.put("/subscription-groups")
async def write_group(
    body: ResourceWrite[SubscriptionGroupResource], app: Application
) -> SourceResult:
    return await write_resource(body, app, "subscription_groups")


async def write_resource(
    body: ResourceWrite[ModelResource] | ResourceWrite[SubscriptionGroupResource],
    app: Application,
    directory: str,
) -> SourceResult:
    if not body.relative_path.startswith(f"{directory}/"):
        raise ValueError(f"Choose a source within {directory}")
    async with app.lock:
        await check_source(app, body.relative_path, body.expected_digest)
        result = await app.host.mutate_configuration(
            relative_path=body.relative_path,
            request=ResourceMutationRequest(
                content=yaml.safe_dump(body.resource.model_dump(mode="json"), sort_keys=False)
            ),
        )
    return SourceResult(
        action=result.action, relative_path=result.relative_path, source_digest=result.source_digest
    )


@router.delete("/models")
async def delete_model(body: ResourceDelete, app: Application) -> SourceResult:
    return await delete_resource(body, app, "models")


@router.delete("/subscription-groups")
async def delete_group(body: ResourceDelete, app: Application) -> SourceResult:
    return await delete_resource(body, app, "subscription_groups")


async def delete_resource(body: ResourceDelete, app: Application, directory: str) -> SourceResult:
    if not body.relative_path.startswith(f"{directory}/"):
        raise ValueError(f"Choose a source within {directory}")
    async with app.lock:
        await check_source(app, body.relative_path, body.expected_digest)
        result = await app.host.delete_configuration(relative_path=body.relative_path)
    return SourceResult(
        action=result.action, relative_path=result.relative_path, source_digest=result.source_digest
    )


@router.get("/credentials")
async def credentials(app: Application) -> tuple[ApiKeyStatus, ...]:
    return await app.host.list_api_keys()


@router.put("/credentials")
async def put_credential(body: ApiKeyInput, app: Application) -> ApiKeyStatus:
    return await app.host.put_api_key(body)
