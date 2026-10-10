// @vitest-environment jsdom
import { useState } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import {
  Api,
  ApiError,
  useRemote,
  type Input,
  type Principal,
  type Resource,
  type Run,
  type Thread,
} from "./api";
import { CommandProvider } from "./commands";
import { Conversation, blankDraft } from "./conversation";
import { NewThread } from "./creation";

const actor: Principal = {
  id: "operator",
  version: 1,
  admin: true,
  enabled: true,
  actions: [],
  thread_ids: [],
  profile_ids: [],
};
const profile: Resource = {
  id: "default",
  kind: "profile",
  version: 1,
  content: {},
  retired: false,
};
const thread: Thread = {
  id: "thread-one",
  title: "Original",
  version: 1,
  profile_id: "default",
  checkpoint_id: "checkpoint-one",
  parent_thread_id: null,
  parent_run_id: null,
  archived: false,
  created_at: "2026-10-10",
};
const run: Run = {
  id: "run-one",
  thread_id: thread.id,
  status: "completed",
  composition: {
    profile,
    model: profile,
    environment: profile,
    workspace: "/workspace",
  },
  checkpoint_id: "checkpoint-one",
  recovery_of: null,
  recovery_required: false,
  reconciled_at: null,
  cancel_requested: false,
  output: "Saved",
  error: null,
  created_at: "2026-10-10",
};
const receipt: Input = {
  id: "input-one",
  run_id: run.id,
  actor_id: actor.id,
  request_id: "",
  text: "Hello",
  source: "console",
  disposition: "incorporated",
  attachment_ids: [],
};
function mockApi() {
  const api = new Api("fixture");
  vi.spyOn(api, "get").mockImplementation(
    async (path) => (path.endsWith("/runs") ? [run] : []) as never,
  );
  return api;
}
function View({
  api,
  snapshot = thread,
  active = true,
}: {
  api: Api;
  snapshot?: Thread;
  active?: boolean;
}) {
  const [draft, setDraft] = useState(blankDraft);
  const [shown, setShown] = useState(true);
  return (
    <CommandProvider>
      <button onClick={() => setShown((value) => !value)}>Switch Thread</button>
      <button
        onClick={() =>
          setDraft((current) => ({
            ...current,
            text: "Newer text",
            separate: true,
          }))
        }
      >
        Edit current draft
      </button>
      <button
        onClick={() =>
          setDraft((current) => ({
            ...blankDraft(),
            generation: current.generation + 1,
            text: "Next generation",
          }))
        }
      >
        Replace draft
      </button>
      {shown && (
        <Conversation
          api={api}
          actor={actor}
          thread={snapshot}
          active={active}
          profiles={[profile]}
          draft={draft}
          setDraft={setDraft}
          changed={() => {}}
          navigate={() => {}}
        />
      )}
    </CommandProvider>
  );
}
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("query rejection after a lost admission keeps the original command through remount", async () => {
  const api = mockApi();
  const send = vi
    .spyOn(api, "send")
    .mockRejectedValue(new ApiError(0, "lost", "Lost reply"));
  render(<View api={api} />);
  fireEvent.change(screen.getByLabelText("Message"), {
    target: { value: "Hello" },
  });
  fireEvent.click(screen.getByText("Send message"));
  await screen.findByText("Lost reply");
  const original = send.mock.calls[0][1] as { request_id: string };
  vi.mocked(api.get).mockRejectedValue(
    new ApiError(403, "forbidden", "Read revoked"),
  );
  fireEvent.click(screen.getByText("Reconcile / retry same input"));
  await screen.findByText("Read revoked");
  fireEvent.click(screen.getByText("Switch Thread"));
  fireEvent.click(screen.getByText("Switch Thread"));
  expect(
    (screen.getByLabelText("Message") as HTMLTextAreaElement).disabled,
  ).toBe(true);
  vi.mocked(api.get).mockImplementation(
    async (path) =>
      (path.endsWith("/inputs")
        ? [{ ...receipt, request_id: original.request_id }]
        : path.endsWith("/runs")
          ? [run]
          : []) as never,
  );
  fireEvent.click(screen.getByText("Reconcile / retry same input"));
  await screen.findByText(/Input accepted/);
  expect(send).toHaveBeenCalledTimes(1);
  expect((screen.getByLabelText("Message") as HTMLTextAreaElement).value).toBe(
    "",
  );
});

test("a first explicit rejection unlocks the input for correction", async () => {
  const api = mockApi();
  vi.spyOn(api, "send").mockRejectedValue(
    new ApiError(422, "invalid", "Correct input"),
  );
  render(<View api={api} />);
  fireEvent.change(screen.getByLabelText("Message"), {
    target: { value: "Hello" },
  });
  fireEvent.click(screen.getByText("Send message"));
  await screen.findByText("Correct input");
  expect(
    (screen.getByLabelText("Message") as HTMLTextAreaElement).disabled,
  ).toBe(false);
});

test.each([false, true])(
  "late upload preserves current draft (new generation: %s)",
  async (replace) => {
    const api = mockApi();
    let finish!: (response: Response) => void;
    vi.spyOn(api, "raw").mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    render(<View api={api} />);
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Old text" },
    });
    fireEvent.change(screen.getByLabelText("Upload file (8 MiB maximum)"), {
      target: { files: [new File(["content"], "one.txt")] },
    });
    fireEvent.click(screen.getByText("Switch Thread"));
    fireEvent.click(screen.getByText("Switch Thread"));
    expect(
      (screen.getByText("Submitting…") as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.click(
      screen.getByText(replace ? "Replace draft" : "Edit current draft"),
    );
    await act(async () =>
      finish(new Response(JSON.stringify({ id: "asset-one" }))),
    );
    expect(
      (screen.getByLabelText("Message") as HTMLTextAreaElement).value,
    ).toBe(replace ? "Next generation" : "Newer text");
    const send = vi.spyOn(api, "send").mockResolvedValue(receipt);
    fireEvent.click(screen.getByText("Send message"));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(send.mock.calls[0][1]).toMatchObject({
      attachment_ids: replace ? [] : ["asset-one"],
      separate_run: !replace,
    });
  },
);

test("upload retry retains the identical File and request URL across remount", async () => {
  const api = mockApi();
  const raw = vi
    .spyOn(api, "raw")
    .mockRejectedValue(new ApiError(0, "lost", "Lost upload"));
  render(<View api={api} />);
  const file = new File(["immutable bytes"], "one.txt", { type: "text/plain" });
  fireEvent.change(screen.getByLabelText("Upload file (8 MiB maximum)"), {
    target: { files: [file] },
  });
  await screen.findByText("Lost upload");
  fireEvent.click(screen.getByText("Switch Thread"));
  fireEvent.click(screen.getByText("Switch Thread"));
  raw.mockResolvedValue(new Response(JSON.stringify({ id: "asset-one" })));
  fireEvent.click(screen.getByText("Retry same upload: one.txt"));
  await waitFor(() => expect(raw).toHaveBeenCalledTimes(2));
  expect(raw.mock.calls[1][0]).toBe(raw.mock.calls[0][0]);
  expect(raw.mock.calls[1][1]?.body).toBe(file);
});

test("settings keep original fields and version when the live Thread refreshes", async () => {
  const api = mockApi();
  const send = vi.spyOn(api, "send").mockResolvedValue(thread);
  const view = render(<View api={api} />);
  fireEvent.click(screen.getByText("Thread settings"));
  fireEvent.change(screen.getByLabelText("Title"), {
    target: { value: "My edit" },
  });
  view.rerender(
    <View
      api={api}
      snapshot={{ ...thread, title: "Concurrent edit", version: 2 }}
    />,
  );
  expect(
    (screen.getByText("Thread settings") as HTMLButtonElement).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByText("Save settings"));
  await waitFor(() => expect(send).toHaveBeenCalled());
  expect(send.mock.calls[0][1]).toMatchObject({
    title: "My edit",
    expected_version: 1,
  });
});

test.each(["create", "fork"])(
  "%s retries immutable payload after a form remount",
  async (kind) => {
    const api = mockApi();
    const send = vi
      .spyOn(api, "send")
      .mockRejectedValue(new ApiError(0, "lost", "Lost creation"));
    function CreationView() {
      const [shown, show] = useState(true);
      return (
        <CommandProvider>
          <button onClick={() => show((value) => !value)}>Toggle form</button>
          {shown && (
            <NewThread
              api={api}
              profiles={[profile]}
              done={() => {}}
              cancel={() => show(false)}
            />
          )}
        </CommandProvider>
      );
    }
    if (kind === "create") render(<CreationView />);
    else render(<View api={api} />);
    if (kind === "create")
      fireEvent.change(screen.getByLabelText("Title"), {
        target: { value: "Identified Thread" },
      });
    else await screen.findByText("Create fork");
    fireEvent.click(
      screen.getByText(kind === "create" ? "Create Thread" : "Create fork"),
    );
    await screen.findAllByText("Lost creation");
    if (kind === "fork")
      vi.mocked(api.get).mockImplementation(
        async (path) =>
          (path.endsWith("/runs")
            ? [{ ...run, checkpoint_id: "checkpoint-newer" }]
            : []) as never,
      );
    const toggle = kind === "create" ? "Toggle form" : "Switch Thread";
    fireEvent.click(screen.getByText(toggle));
    fireEvent.click(screen.getByText(toggle));
    await screen.findByText(
      kind === "create" ? "Retry same creation" : "Retry same fork",
    );
    send.mockResolvedValue(thread);
    fireEvent.click(
      screen.getByText(
        kind === "create" ? "Retry same creation" : "Retry same fork",
      ),
    );
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(send.mock.calls[1][1]).toEqual(send.mock.calls[0][1]);
    expect(
      (send.mock.calls[0][1] as { request_id: string }).request_id,
    ).toBeTruthy();
  },
);

test("paused queries keep saved data and stop fetching until visible again", async () => {
  vi.useFakeTimers();
  const api = mockApi();
  function Query({ active }: { active: boolean }) {
    const result = useRemote<Run[]>(api, "/runs", 100, active);
    return <span>{result.data?.[0].output}</span>;
  }
  const view = render(<Query active />);
  await act(async () => {});
  expect(screen.getByText("Saved")).toBeTruthy();
  view.rerender(<Query active={false} />);
  vi.mocked(api.get).mockClear();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(api.get).not.toHaveBeenCalled();
  expect(screen.getByText("Saved")).toBeTruthy();
  view.rerender(<Query active />);
  await act(async () => {});
  expect(api.get).toHaveBeenCalledTimes(1);
});

test("collapsed history never queries children; expanded live children keep polling after parent completion", async () => {
  vi.useFakeTimers();
  const api = mockApi();
  const child = {
    id: "child",
    child_thread_id: "thread-child",
    status: "running",
    output: null,
    error: null,
    cancel_policy: "keep",
    notify: true,
    delivery_input_id: null,
    delivery_error: null,
  };
  vi.mocked(api.get).mockImplementation(
    async (path) =>
      (path.endsWith("/runs")
        ? [run]
        : path.endsWith("/children")
          ? [child]
          : []) as never,
  );
  const view = render(<View api={api} />);
  await act(async () => {});
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2100);
  });
  const childCalls = () =>
    vi.mocked(api.get).mock.calls.filter(([path]) => path.endsWith("/children"))
      .length;
  expect(childCalls()).toBe(0);
  const details = screen.getByText(/Execution details/).closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  await act(async () => {});
  expect(childCalls()).toBe(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2100);
  });
  expect(childCalls()).toBe(2);
  child.status = "completed";
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2100);
  });
  expect(childCalls()).toBe(3);
  child.delivery_input_id = "delivered" as never;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2100);
  });
  const settled = childCalls();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(childCalls()).toBe(settled);
  view.rerender(<View api={api} active={false} />);
  vi.mocked(api.get).mockClear();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(api.get).not.toHaveBeenCalled();
});
