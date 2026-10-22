"""Trusted operator API composing public Host operations and Claw business services."""

from __future__ import annotations

from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from fastapi import APIRouter

from a13n_claw.api.dependencies import Application
from a13n_claw.configuration.models import ProductModel

router = APIRouter()


class ConfigurationWrite(ProductModel):
    relative_path: str
    content: str
    expected_digest: str | None  # null means a new source


class ProductWrite(ProductModel):
    content: str
    expected_digest: str


class ProductReload(ProductModel):
    expected_digest: str


@router.get("/configuration")
async def configuration(app: Application):
    return await app.host.configuration_sources()


@router.get("/configuration/source")
async def configuration_source(app: Application, path: str):
    return await app.host.configuration_source(relative_path=path)


@router.put("/configuration/source")
async def write_configuration(body: ConfigurationWrite, app: Application):
    # Claw serializes its editors and checks the observed source. Native Host mutation
    # validates/accepts the candidate; it does not expose an OS-wide compare-and-swap.
    async with app.lock:
        catalog = await app.host.configuration_sources()
        existing = next((s for s in catalog.sources if s.relative_path == body.relative_path), None)
        if existing is None:
            if body.expected_digest is not None:
                raise ValueError("Configuration source no longer exists")
        else:
            source = await app.host.configuration_source(relative_path=body.relative_path)
            if source.source_digest != body.expected_digest:
                raise ValueError("Configuration source changed; refetch and preserve your draft")
        return await app.host.mutate_configuration(
            relative_path=body.relative_path, request=ResourceMutationRequest(content=body.content)
        )


@router.post("/configuration/reload")
async def reload_configuration(app: Application):
    async with app.lock:
        await app.host.reload_configuration()
        return await app.host.configuration_sources()


@router.get("/product-configuration")
async def product_configuration(app: Application):
    return await app.configuration.view()


@router.put("/product-configuration")
async def write_product_configuration(body: ProductWrite, app: Application):
    return await app.configuration.save(body.content, body.expected_digest)


@router.post("/product-configuration/reload")
async def reload_product_configuration(body: ProductReload, app: Application):
    return await app.configuration.reload(body.expected_digest)


@router.get("/projects")
async def projects(app: Application):
    return await app.host.projects()
