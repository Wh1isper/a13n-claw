"""Account lifecycle and provider allowances owned by the embedded Host."""

from typing import Literal

from a13n_harness_ui.model_accounts.login import LoginCallbackInput, LoginRequest, LoginStatus
from a13n_harness_ui.model_accounts.managed import SubscriptionAccount
from a13n_harness_ui.model_accounts.models import (
    AccountCandidate,
    AccountProjection,
    AccountSelection,
)
from a13n_harness_ui.model_accounts.provider_usage import CopilotUsage
from a13n_harness_ui.model_accounts.subscriptions import (
    SubscriptionAccountCreate,
    SubscriptionAccountUpdate,
    SubscriptionCatalog,
)
from a13n_harness_ui.model_accounts.usage import CodexUsage, ResetRequest, ResetResult
from a13n_harness_ui.model_accounts.usage_cache import UsageSnapshot
from a13n_harness_ui.model_authoring import ModelChoice
from fastapi import APIRouter, Response

from a13n_claw.api.dependencies import Application

router = APIRouter()
type SelectedProvider = Literal["chatgpt", "copilot"]


@router.get("/subscriptions")
async def subscriptions(app: Application) -> SubscriptionCatalog:
    return await app.host.subscriptions()


@router.post("/subscriptions/accounts", status_code=201)
async def create_account(body: SubscriptionAccountCreate, app: Application) -> SubscriptionAccount:
    return await app.host.create_subscription_account(body)


@router.patch("/subscriptions/accounts/{account_id}")
async def update_account(
    account_id: str, body: SubscriptionAccountUpdate, app: Application
) -> SubscriptionAccount:
    return await app.host.update_subscription_account(account_id, body)


@router.delete("/subscriptions/accounts/{account_id}", status_code=204)
async def delete_account(account_id: str, app: Application) -> Response:
    await app.host.delete_subscription_account(account_id)
    return Response(status_code=204)


@router.post("/subscriptions/accounts/{account_id}/logout")
async def logout_account(account_id: str, app: Application) -> bool:
    return await app.host.logout_subscription_account(account_id)


@router.post("/subscriptions/accounts/{account_id}/refresh")
async def refresh_usage(account_id: str, app: Application) -> UsageSnapshot:
    return await app.host.refresh_subscription_usage(account_id)


@router.get("/subscriptions/accounts/{account_id}/codex")
async def codex_usage(account_id: str, app: Application) -> CodexUsage:
    return await app.host.codex_usage(account_id=account_id)


@router.post("/subscriptions/accounts/{account_id}/reset")
async def redeem_reset(account_id: str, body: ResetRequest, app: Application) -> ResetResult:
    return await app.host.redeem_codex_reset(body, account_id=account_id)


@router.get("/accounts/{provider}")
async def inspect_account(provider: SelectedProvider, app: Application) -> AccountProjection:
    return await app.host.inspect_model_account(provider)


@router.get("/accounts/{provider}/candidates")
async def account_candidates(
    provider: SelectedProvider, app: Application
) -> tuple[AccountCandidate, ...]:
    return await app.host.model_account_candidates(provider)


@router.put("/accounts/{provider}/selection")
async def select_account(
    provider: SelectedProvider, body: AccountSelection, app: Application
) -> AccountProjection:
    return await app.host.select_model_account(provider, body)


@router.post("/accounts/{provider}/logout")
async def logout_selected_account(provider: SelectedProvider, app: Application) -> bool:
    return await app.host.logout_model_account(provider)


@router.post("/accounts/{provider}/models")
async def discover_models(provider: SelectedProvider, app: Application) -> tuple[ModelChoice, ...]:
    return await app.host.discover_account_models(provider)


@router.get("/accounts/copilot/usage")
async def copilot_usage(app: Application) -> CopilotUsage:
    return await app.host.copilot_usage()


@router.get("/login")
async def active_login(app: Application) -> LoginStatus | None:
    return await app.host.active_login()


@router.post("/login", status_code=202)
async def start_login(body: LoginRequest, app: Application) -> LoginStatus:
    return await app.host.start_login(body)


@router.get("/login/{session_id}")
async def login_status(session_id: str, app: Application) -> LoginStatus:
    return await app.host.login_status(session_id)


@router.post("/login/{session_id}/callback")
async def login_callback(
    session_id: str, body: LoginCallbackInput, app: Application
) -> LoginStatus:
    return await app.host.submit_login_callback(session_id, body.callback_url.get_secret_value())


@router.post("/login/{session_id}/cancel")
async def cancel_login(session_id: str, app: Application) -> LoginStatus:
    return await app.host.cancel_login(session_id)
