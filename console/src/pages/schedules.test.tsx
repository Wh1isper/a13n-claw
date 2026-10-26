// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
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
import { ScheduleEditor } from "./schedule-editor";
import { ManualTrigger } from "./schedules";

const api = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn() }));
vi.mock("../api/session", () => ({ useApi: () => api }));
let cache: QueryClient;
beforeEach(() => {
  vi.resetAllMocks();
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  api.GET.mockResolvedValue({ data: [] });
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
const definition: Schema["ScheduleDefinition"] = {
  name: "Daily",
  prompt: "Summarize work",
  project_id: "project-main",
  enabled: true,
  timing: { kind: "cron", cron: "0 9 * * *", timezone: "Asia/Shanghai" },
  notification: "always",
  destination: { channel_id: "team", reply_to: "topic" },
  author: "ou-operator",
  source_channel: "team",
  source: {
    connection: "work",
    app_id: "app",
    domain: "feishu",
    chat_id: "chat",
  },
  overlap: "skip",
  missed: "latest",
  grace_seconds: 120,
};
it("preserves author/source/options and original catalog revision on edit conflicts", async () => {
  api.PUT.mockRejectedValue(
    new ApiError(400, "invalid_request", "Schedule source changed"),
  );
  const close = vi.fn();
  mount(
    <ScheduleEditor
      id="daily"
      definition={definition}
      digest="original"
      close={close}
    />,
  );
  fireEvent.change(screen.getByLabelText("Name"), {
    target: { value: "Updated daily" },
  });
  await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
  await screen.findByRole("alert");
  expect(api.PUT.mock.calls[0][1].body).toEqual({
    definition: { ...definition, name: "Updated daily" },
    expected_digest: "original",
  });
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
    "Updated daily",
  );
  expect(close).not.toHaveBeenCalled();
});
it("shows server timing offsets and discards stale preview after timing edits", async () => {
  api.POST.mockResolvedValue({
    data: {
      timezone: "Asia/Shanghai",
      instants: ["2026-10-27T09:00:00+08:00"],
    },
  });
  mount(
    <ScheduleEditor
      id="daily"
      definition={definition}
      digest="original"
      close={vi.fn()}
    />,
  );
  await userEvent.click(screen.getByRole("button", { name: "Preview timing" }));
  await screen.findByText("2026-10-27T09:00:00+08:00");
  fireEvent.change(screen.getByLabelText(/Cron expression/), {
    target: { value: "0 10 * * *" },
  });
  expect(screen.queryByText("2026-10-27T09:00:00+08:00")).toBeNull();
});
it("retains one trigger identity through uncertain transport and dialog remount", async () => {
  api.POST.mockRejectedValueOnce(new TypeError("Connection lost"));
  const props = { scheduleId: "daily", close: vi.fn(), inspect: vi.fn() };
  const first = mount(<ManualTrigger {...props} />);
  await userEvent.click(
    screen.getByRole("button", { name: "Confirm run now" }),
  );
  await screen.findByText("Connection lost");
  const request = api.POST.mock.calls[0][1].body;
  expect(request.request_key).toBeTruthy();
  first.unmount();
  api.POST.mockResolvedValue({ data: { id: "occurrence-original" } });
  mount(<ManualTrigger {...props} />);
  await userEvent.click(
    screen.getByRole("button", { name: "Retry the same request" }),
  );
  await screen.findByRole("button", { name: "Inspect occurrence" });
  expect(api.POST.mock.calls[1][1].body).toEqual(request);
  await userEvent.click(
    screen.getByRole("button", { name: "Inspect occurrence" }),
  );
  expect(props.inspect).toHaveBeenCalledWith("occurrence-original");
  await waitFor(() => expect(api.POST).toHaveBeenCalledTimes(2));
});
