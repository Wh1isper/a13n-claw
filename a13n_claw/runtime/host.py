"""The concrete, package-embedded Harness UI boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from a13n_harness_ui.app import HarnessUiApp, HarnessUiIntegrations, open_harness_ui_app
from a13n_harness_ui.settings_loader import load_harness_ui_settings

from a13n_claw.configuration.models import ClawSettings, reject_standalone_root, selected_path


@asynccontextmanager
async def open_host(
    root: Path,
    settings: ClawSettings,
    *,
    integrations: HarnessUiIntegrations | None = None,
) -> AsyncIterator[HarnessUiApp]:
    """The caller owns the Instance lease and keeps product services alive until exit."""
    reject_standalone_root(root)
    source = await load_harness_ui_settings(
        selected_path(root, settings.host_configuration),
        data_root=selected_path(root, settings.host_data),
    )
    if source.candidate_error is not None:
        raise source.candidate_error
    async with open_harness_ui_app(
        source.settings,
        configuration_path=source.path,
        host_mode="webui",
        mcp_input_enabled=False,
        share_computer=False,
        integrations=integrations,
        instrumentation=None,
    ) as app:
        yield app
