// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ApiError, type Schema } from "../api/client";
import { ModelEditor } from "./model-editor";
import { LoginFlow, LoginMonitor } from "../components/account-login";
import { AccountReset, availableCredits } from "../components/account-reset";

const api = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn() }));
vi.mock("../api/session", () => ({ useApi: () => api }));
let cache: QueryClient;
beforeEach(() => {
  vi.resetAllMocks();
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
});
afterEach(() => {
  cleanup();
  cache.clear();
});
function mount(children: ReactNode) {
  return render(
    <QueryClientProvider client={cache}>{children}</QueryClientProvider>,
  );
}
const connection: Schema["ModelConnection"] = {
  id: "openai-chat",
  label: "OpenAI chat",
  provider: "openai-chat",
  authentication: "api_key",
  models: [],
  default_model: "gpt-4o",
  supports_base_url: true,
  base_url: "",
  supports_session_affinity: false,
  credential_env: "OPENAI_API_KEY",
};
const choices: Schema["ModelChoices"] = {
  connections: [connection],
  session_affinity_presets: [],
};
const view: Schema["ModelInventory"]["models"][number] = {
  relative_path: "models/custom.yaml",
  source_digest: "original-digest",
  resource: {
    schema_version: "1",
    kind: "model",
    id: "model-custom",
    name: "Custom",
    route: "openai-chat:gpt-4o",
    authentication: { kind: "api_key", credential_ref: "key-ref" },
    settings: { temperature: 0.25, vendor_option: { nested: true } },
    model_configuration: {
      base_url: "https://example.test/v1",
      headers: { "x-session": "kept" },
    },
  },
};
function modelApi() {
  api.POST.mockImplementation(async (path, { body }) => ({
    data: path.endsWith("options")
      ? {
          presets: [
            {
              value: "balanced",
              label: "Balanced",
              description: "Balanced settings",
            },
          ],
          context_window: 1000,
        }
      : {
          route: view.resource.route,
          authentication: body.authentication,
          settings: body.settings ?? { temperature: 0.5 },
          model_configuration: {
            ...body.model_configuration,
            ...(body.base_url ? { base_url: body.base_url } : {}),
          },
        },
  }));
  api.PUT.mockResolvedValue({ data: { action: "updated" } });
}
it("preserves native settings, credential references and source preconditions", async () => {
  modelApi();
  const user = userEvent.setup();
  mount(
    <ModelEditor view={view} choices={choices} groups={[]} close={vi.fn()} />,
  );
  await user.clear(screen.getByLabelText("Name"));
  await user.type(screen.getByLabelText("Name"), "Renamed");
  await user.click(screen.getByRole("button", { name: "Save model" }));
  await waitFor(() => expect(api.PUT).toHaveBeenCalledOnce());
  expect(api.PUT.mock.calls[0][1].body).toMatchObject({
    relative_path: view.relative_path,
    expected_digest: view.source_digest,
    resource: { ...view.resource, name: "Renamed" },
  });
});
it("keeps the draft when source CAS rejects the save", async () => {
  modelApi();
  api.PUT.mockRejectedValue(
    new ApiError(409, "source_conflict", "Source changed"),
  );
  const close = vi.fn();
  const user = userEvent.setup();
  mount(
    <ModelEditor view={view} choices={choices} groups={[]} close={close} />,
  );
  fireEvent.change(screen.getByLabelText("Name"), {
    target: { value: "Unsaved draft" },
  });
  await user.click(screen.getByRole("button", { name: "Save model" }));
  await screen.findByText(/Your draft is still here/);
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
    "Unsaved draft",
  );
  expect(close).not.toHaveBeenCalled();
});
it("does not silently convert unsupported routes to the first recipe", () => {
  mount(
    <ModelEditor
      view={{
        ...view,
        resource: { ...view.resource, route: "custom-native:model" },
      }}
      choices={choices}
      groups={[]}
      close={vi.fn()}
    />,
  );
  expect(screen.getByText(/uses a custom connection/)).toBeDefined();
  expect(screen.queryByRole("button", { name: "Save model" })).toBeNull();
  expect(api.POST).not.toHaveBeenCalled();
});
it("applies presets without parsing ignored custom JSON and supports clearing base URL", async () => {
  modelApi();
  const user = userEvent.setup();
  mount(
    <ModelEditor view={view} choices={choices} groups={[]} close={vi.fn()} />,
  );
  await user.click(screen.getByText("Advanced settings"));
  await user.clear(screen.getByLabelText(/Model settings/));
  await user.type(screen.getByLabelText(/Model settings/), "invalid JSON");
  await user.selectOptions(
    await screen.findByLabelText(/Settings preset/),
    "balanced",
  );
  await user.clear(screen.getByLabelText(/^Base URL/));
  await user.click(screen.getByRole("button", { name: "Save model" }));
  await waitFor(() => expect(api.PUT).toHaveBeenCalledOnce());
  const request = api.POST.mock.calls.find(([path]) =>
    path.endsWith("prepare"),
  )![1].body;
  expect(request).toMatchObject({
    preset: "balanced",
    settings: null,
    base_url: null,
  });
  expect(request.model_configuration).toEqual({
    headers: { "x-session": "kept" },
  });
});
const waiting: Schema["LoginStatus"] = {
  session_id: "login-test",
  provider: "codex",
  method: "device",
  state: "waiting",
  expires_in: 900,
};
it("prefers device authorization and closing does not cancel it", async () => {
  api.POST.mockResolvedValue({ data: waiting });
  api.GET.mockResolvedValue({ data: waiting });
  const user = userEvent.setup();
  const close = vi.fn();
  mount(
    <LoginFlow
      connection={{
        provider: "codex",
        label: "Codex",
        login_methods: ["browser", "device"],
        source_selection: false,
        model_discovery: false,
      }}
      accountId="local-account"
      close={close}
    />,
  );
  expect(
    (screen.getByLabelText("Sign-in method") as HTMLSelectElement).value,
  ).toBe("device");
  await user.click(screen.getByRole("button", { name: "Start authorization" }));
  await screen.findByRole("button", { name: "Cancel authorization" });
  await user.click(screen.getByRole("button", { name: "Close dialog" }));
  expect(close).toHaveBeenCalledOnce();
  expect(api.POST).toHaveBeenCalledOnce();
  expect(api.POST.mock.calls[0][1].body).toMatchObject({
    method: "device",
    account_id: "local-account",
  });
});
it("refreshes identity and quota when authorization finishes outside its dialog", async () => {
  cache.setQueryData(["active-login"], waiting);
  cache.setQueryData(["account", "copilot"], { usable: false });
  cache.setQueryData(["copilot-usage", "previous"], { remaining: 1 });
  api.GET.mockResolvedValue({ data: waiting });
  mount(<LoginMonitor />);
  await waitFor(() => expect(api.GET).toHaveBeenCalled());
  await act(async () => {
    cache.setQueryData(["active-login"], null);
  });
  await waitFor(() =>
    expect(cache.getQueryState(["account", "copilot"])?.isInvalidated).toBe(
      true,
    ),
  );
  expect(cache.getQueryData(["copilot-usage", "previous"])).toBeUndefined();
});
const usage: Schema["CodexUsage"] = {
  account_id: "upstream-identity",
  plan_type: "pro",
  provider: "codex",
  additional_rate_limits: [],
  reset_credits: {
    available_count: 1,
    credits: [
      {
        id: "voucher-one",
        status: "available",
        reset_type: "codex_rate_limits",
      },
    ],
  },
};
it("keeps redemption identity across an ambiguous result and remount; refresh failure cannot undo confirmation", async () => {
  api.GET.mockResolvedValue({ data: usage });
  api.POST.mockRejectedValueOnce(new TypeError("Connection lost"));
  const account: Schema["SubscriptionAccount"] = {
    id: "local-identity",
    provider: "codex",
    name: "Test",
    enabled: true,
    deleted: false,
    login_revision: 0,
  };
  const user = userEvent.setup();
  const first = mount(<AccountReset account={account} close={vi.fn()} />);
  await user.selectOptions(
    await screen.findByLabelText("Reset voucher"),
    "voucher-one",
  );
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Redeem voucher" }));
  await screen.findByText("Connection lost");
  const original = api.POST.mock.calls[0][1];
  expect(original.params.path.account_id).toBe("local-identity");
  expect(original.body.account_id).toBe("upstream-identity");
  expect(original.body.credit_id).toBe("voucher-one");
  first.unmount();
  api.POST.mockResolvedValueOnce({ data: { code: "reset", windows_reset: 2 } });
  api.GET.mockRejectedValue(new Error("Quota unavailable"));
  mount(<AccountReset account={account} close={vi.fn()} />);
  await user.click(
    screen.getByRole("button", { name: "Retry the same request" }),
  );
  await screen.findByText(/Provider confirmed: reset/);
  expect(api.POST.mock.calls[1][1]).toEqual(original);
  await screen.findByText(/Redemption remains confirmed/);
});
it("offers only unexpired, available Codex vouchers", () => {
  expect(
    availableCredits(
      {
        ...usage,
        reset_credits: {
          available_count: 1,
          credits: [
            ...usage.reset_credits!.credits,
            {
              id: "expired",
              status: "available",
              reset_type: "codex_rate_limits",
              expires_at: "2020-01-01T00:00:00Z",
            },
            {
              id: "spent",
              status: "redeemed",
              reset_type: "codex_rate_limits",
            },
            { id: "other", status: "available", reset_type: "other" },
          ],
        },
      },
      Date.parse("2026-10-26T00:00:00Z"),
    ).map((item) => item.id),
  ).toEqual(["voucher-one"]);
});
