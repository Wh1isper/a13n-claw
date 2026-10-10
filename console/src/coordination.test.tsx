// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Api, ApiError, type Principal } from "./api";
import { CommandProvider } from "./commands";
import {
  CoordinationPage,
  type CoordinationState,
  type InboxItem,
} from "./coordination";

const actor: Principal = {
  id: "operator",
  version: 1,
  admin: true,
  enabled: true,
  actions: [],
  thread_ids: [],
  profile_ids: [],
};
const initial: CoordinationState = {
  mode: "one_thread",
  main_id: "main",
  paused: true,
  version: 2,
  failures: 0,
  retry_at: null,
  blocked_reason: "paused",
  counts: { pending: 1, deferred: 2 },
  workers: [],
};
const original: InboxItem = {
  id: "item",
  sequence: 1,
  kind: "external",
  disposition: "pending",
  version: 1,
  channel_id: "room",
  payload: { text: "Review this message" },
  note: "",
  dependency_run_id: null,
  due_at: null,
  human_key: null,
};
function fixture() {
  const api = new Api("fixture");
  const saved = { state: initial, item: original };
  vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path === "/coordination") return saved.state as never;
    if (path.startsWith("/inbox")) return [saved.item] as never;
    if (path === "/deliveries")
      return [
        {
          id: "delivery",
          thread_id: "main",
          channel_id: "room",
          text: "Saved reply",
          status: "unknown",
          version: 3,
          error: "process_interrupted",
          transport_receipt: null,
        },
      ] as never;
    return [] as never;
  });
  return { api, saved };
}
function Page({ api, active = true }: { api: Api; active?: boolean }) {
  return (
    <CommandProvider>
      <CoordinationPage
        api={api}
        actor={actor}
        profiles={[]}
        active={active}
        navigate={() => {}}
      />
    </CommandProvider>
  );
}
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("polling cannot replace the Inbox editor's original CAS version or note", async () => {
  const { api, saved } = fixture();
  const send = vi
    .spyOn(api, "send")
    .mockRejectedValue(
      new ApiError(409, "version_conflict", "Inbox item changed"),
    );
  render(<Page api={api} />);
  fireEvent.click(await screen.findByText("Review item"));
  fireEvent.change(screen.getByLabelText("Result or reason"), {
    target: { value: "My reviewed result" },
  });
  saved.item = {
    ...original,
    version: 3,
    disposition: "pending",
    note: "Worker finished",
  };
  fireEvent.click(screen.getByText("Refresh saved state"));
  await screen.findByText("Worker finished");
  fireEvent.click(screen.getByText("Save disposition"));
  await screen.findByText("Inbox item changed");
  expect(send.mock.calls[0][1]).toEqual({
    expected_version: 1,
    disposition: "handled",
    note: "My reviewed result",
  });
  expect(
    (screen.getByLabelText("Result or reason") as HTMLTextAreaElement).value,
  ).toBe("My reviewed result");
});

test("lost pause response reconciles saved control without a second mutation", async () => {
  const { api, saved } = fixture();
  const send = vi.spyOn(api, "send").mockImplementation(async () => {
    saved.state = {
      ...initial,
      paused: false,
      version: 3,
      blocked_reason: null,
    };
    throw new ApiError(0, "lost", "Connection lost");
  });
  render(<Page api={api} />);
  fireEvent.click(await screen.findByText("Resume automatic processing"));
  await screen.findByText("Reconcile pending control");
  fireEvent.click(screen.getByText("Reconcile pending control"));
  await screen.findByText("Automatic processing enabled");
  expect(send).toHaveBeenCalledTimes(1);
  expect(screen.getByText("Review this message")).toBeTruthy();
});

test("unknown delivery requires evidence and defaults to no retry", async () => {
  const { api } = fixture();
  const send = vi.spyOn(api, "send").mockResolvedValue({});
  render(<Page api={api} />);
  await screen.findByText("Automatic processing paused");
  fireEvent.click(screen.getByText("Deliveries"));
  fireEvent.click(await screen.findByText("Reconcile delivery"));
  expect(
    screen.queryByText("Retry the same delivery identity under current policy"),
  ).toBeNull();
  fireEvent.change(screen.getByLabelText("Confirmed outcome"), {
    target: { value: "not_sent" },
  });
  expect(
    (
      screen.getByLabelText(
        "Retry the same delivery identity under current policy",
      ) as HTMLInputElement
    ).checked,
  ).toBe(false);
  fireEvent.change(screen.getByLabelText("Evidence / reconciliation note"), {
    target: { value: "Receiver verified no send" },
  });
  fireEvent.click(screen.getByText("Save reconciliation"));
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
  expect(send.mock.calls[0]).toEqual([
    "/deliveries/delivery/reconcile",
    {
      expected_version: 3,
      outcome: "not_sent",
      retry: false,
      note: "Receiver verified no send",
    },
  ]);
});

test("reconnect shows retained pause and backlog; hidden coordination does not poll", async () => {
  const { api } = fixture();
  const page = render(<Page api={api} />);
  await screen.findByText("Review this message");
  page.unmount();
  const next = render(<Page api={api} />);
  await screen.findByText("Automatic processing paused");
  await screen.findByText("Review this message");
  next.rerender(<Page api={api} active={false} />);
  await act(async () => {});
  vi.useFakeTimers();
  const count = vi.mocked(api.get).mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(vi.mocked(api.get).mock.calls.length).toBe(count);
});
