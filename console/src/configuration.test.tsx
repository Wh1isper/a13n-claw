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
import { Api, ApiError } from "./api";
import { ConfigurationEditor } from "./configuration-editor";
import {
  patchSource,
  readSource,
  type ConfigurationStatus,
} from "./configuration-document";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const source = `# Operator note
format: 1
models:
  primary:
    provider: openai
    model_name: original # model comment
    credential_env: KEY
profiles:
  default:
    model_id: primary
    environment_id: local
    skills:
      - helper # keep this list comment
`;
function status(
  text = source,
  revision = "first",
  active = revision,
): ConfigurationStatus {
  return {
    path: "/claw/config.yaml",
    text,
    revision,
    active_revision: active,
    pending: revision !== active,
    document: readSource(text)?.value ?? null,
    issues: [],
    restart_required: [],
  };
}
function setup(initial = status()) {
  const api = new Api("test");
  const get = vi.spyOn(api, "get").mockResolvedValue(initial);
  const send = vi.spyOn(api, "send");
  const changed = vi.fn();
  const view = render(
    <ConfigurationEditor api={api} active changed={changed} />,
  );
  return { api, get, send, changed, ...view };
}
async function yamlEditor() {
  await screen.findByRole("button", { name: "YAML" });
  fireEvent.click(screen.getByRole("button", { name: "YAML" }));
  return screen.getByLabelText(
    /Full configuration source/,
  ) as HTMLTextAreaElement;
}
function button(name: string) {
  return screen.getByRole("button", { name }) as HTMLButtonElement;
}

test("leaf patches preserve nested and list comments, unrelated values and explicit empty lists", () => {
  const next = patchSource(source, ["models", "primary", "model_name"], "new");
  expect(next).toContain("# Operator note");
  expect(next).toContain("# model comment");
  expect(next).toContain("# keep this list comment");
  expect(readSource(next)?.value.profiles).toEqual(
    readSource(source)?.value.profiles,
  );
  expect(
    readSource(patchSource(next, ["profiles", "default", "skills"], []))?.value,
  ).toHaveProperty("profiles.default.skills", []);
  expect(() => patchSource("models: [", ["format"], 1)).toThrow();
  expect(readSource("format: 1\nformat: 1\n")).toBeNull();
  expect(readSource("server:\n  console: no\n")?.value).toEqual({
    server: { console: false },
  });
});

test("forms and YAML share a draft, late saves preserve typing and loading remains explicit", async () => {
  const { send, changed } = setup();
  await screen.findByLabelText(/Model name/);
  fireEvent.change(screen.getByLabelText(/Model name/), {
    target: { value: "submitted" },
  });
  const editor = await yamlEditor();
  expect(editor.value).toContain("model_name: submitted");
  const submitted = editor.value;
  let resolve!: (value: ConfigurationStatus) => void;
  send.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done as typeof resolve;
      }),
  );
  fireEvent.click(button("Save file"));
  fireEvent.change(editor, {
    target: { value: submitted + "# typed during save\n" },
  });
  await act(async () => resolve(status(submitted, "second", "first")));
  expect(editor.value).toContain("# typed during save");
  expect(button("Save file").disabled).toBe(false);
  expect(button("Load saved file").disabled).toBe(true);
  expect(changed).not.toHaveBeenCalled();
  expect(send.mock.calls[0]).toEqual([
    "/configuration",
    { text: submitted, expected_revision: "first" },
    "PUT",
  ]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(button("Discard draft"));
  send.mockResolvedValueOnce(status(submitted, "second"));
  fireEvent.click(button("Load saved file"));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(send.mock.calls[1]).toEqual([
    "/configuration/reload",
    { expected_revision: "second" },
  ]);
});

test("external changes preserve the old revision until explicit comparison", async () => {
  const { get, send } = setup();
  const editor = await yamlEditor();
  fireEvent.change(editor, { target: { value: source + "# my draft\n" } });
  get.mockResolvedValueOnce(
    status(source + "# external\n", "external", "first"),
  );
  fireEvent.click(button("Refresh status"));
  await screen.findByText("The file changed outside this draft");
  expect(editor.value).toContain("# my draft");
  expect(button("Save file").disabled).toBe(true);
  expect(send).not.toHaveBeenCalled();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(button("Keep draft on this revision"));
  send.mockResolvedValueOnce(status(editor.value, "third", "first"));
  fireEvent.click(button("Save file"));
  await waitFor(() => expect(send).toHaveBeenCalled());
  expect(send.mock.calls[0][1]).toEqual({
    text: source + "# my draft\n",
    expected_revision: "external",
  });
});

test("lost save response reconciles submitted bytes without discarding newer input", async () => {
  const { get, send } = setup();
  const editor = await yamlEditor();
  const submitted = source + "# saved\n";
  fireEvent.change(editor, { target: { value: submitted } });
  send.mockRejectedValueOnce(
    new ApiError(0, "connection_lost", "Response lost"),
  );
  fireEvent.click(button("Save file"));
  await screen.findByText("Save needs reconciliation");
  fireEvent.change(editor, { target: { value: submitted + "# newer\n" } });
  get.mockResolvedValueOnce(status(submitted, "second", "first"));
  fireEvent.click(button("Refresh status"));
  await waitFor(() =>
    expect(screen.queryByText("Save needs reconciliation")).toBeNull(),
  );
  expect(editor.value).toContain("# newer");
  expect(send).toHaveBeenCalledTimes(1);
  expect(button("Save file").disabled).toBe(false);
});

test("a failed load leaves the saved file visible, retryable and separate from validation", async () => {
  const pending = status(source, "second", "first");
  const { send } = setup(pending);
  await screen.findByRole("button", { name: "Load saved file" });
  send.mockRejectedValueOnce(
    new ApiError(500, "request_failed", "Load failed"),
  );
  fireEvent.click(button("Load saved file"));
  await screen.findByText("Load failed");
  expect(button("Load saved file").disabled).toBe(false);
  expect(screen.getByText("Saved · not loaded")).toBeTruthy();
  send.mockResolvedValueOnce({ valid: true, issues: [], changes: [] });
  fireEvent.click(button("Validate & preview"));
  await screen.findByText("Draft validated");
  expect(send.mock.calls[1][0]).toBe("/configuration/validate");
});

test("malformed external YAML remains repairable and hidden drafts survive navigation", async () => {
  const { api, rerender, changed } = setup(
    status("models: [broken\n", "broken", "first"),
  );
  await screen.findByText("Repair the source to continue");
  const editor = await yamlEditor();
  fireEvent.change(editor, { target: { value: source + "# repair\n" } });
  rerender(<ConfigurationEditor api={api} active={false} changed={changed} />);
  rerender(<ConfigurationEditor api={api} active changed={changed} />);
  await waitFor(() => expect(editor.value).toContain("# repair"));
  fireEvent.click(button("Form"));
  expect((screen.getByLabelText(/Model name/) as HTMLInputElement).value).toBe(
    "original",
  );
});

test.each(["save", "load"])(
  "a late navigation refresh cannot undo a completed %s",
  async (operation) => {
    const { api, get, send, changed, rerender } = setup(
      status(source, "first", "older"),
    );
    const editor = await yamlEditor();
    const submitted = operation === "save" ? source + "# saved\n" : source;
    if (operation === "save")
      fireEvent.change(editor, { target: { value: submitted } });
    let finish!: (value: ConfigurationStatus) => void;
    let refresh!: (value: ConfigurationStatus) => void;
    send.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve as typeof finish;
        }),
    );
    fireEvent.click(
      button(operation === "save" ? "Save file" : "Load saved file"),
    );
    get.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          refresh = resolve as typeof refresh;
        }),
    );
    rerender(
      <ConfigurationEditor api={api} active={false} changed={changed} />,
    );
    rerender(<ConfigurationEditor api={api} active changed={changed} />);
    await act(async () =>
      finish(status(submitted, operation === "save" ? "second" : "first")),
    );
    await act(async () => refresh(status(source, "first", "older")));
    expect(
      screen.queryByText("The file changed outside this draft"),
    ).toBeNull();
    expect(
      screen.getByText("Loaded", { selector: ".config-state" }),
    ).toBeTruthy();
  },
);

test("starter definitions and new nested fields create ordinary YAML mappings", async () => {
  setup(status("format: 1\n"));
  fireEvent.click(
    await screen.findByRole("button", { name: "Add starter definitions" }),
  );
  expect((screen.getByLabelText(/Model name/) as HTMLInputElement).value).toBe(
    "replace-with-your-model",
  );
  const editor = await yamlEditor();
  expect(editor.value).not.toContain("!!omap");
  expect(readSource(editor.value)?.value).toHaveProperty(
    "defaults.profile_id",
    "default",
  );
  const nested = patchSource(
    editor.value,
    ["profiles", "default", "permissions", "rules", "claw.files.retain"],
    "ask",
  );
  expect(nested).not.toContain("!!omap");
  expect(readSource(nested)?.value).toHaveProperty(
    "profiles.default.permissions.rules",
    { "claw.files.retain": "ask" },
  );
});

test("Defaults retirement uses the singleton identity and can be reactivated", async () => {
  setup(status(source + "defaults:\n  profile_id: default\n"));
  fireEvent.click(await screen.findByRole("button", { name: "Defaults" }));
  fireEvent.click(screen.getByLabelText("Retire from future selection"));
  let editor = await yamlEditor();
  expect(readSource(editor.value)?.value.retired).toEqual([
    "defaults/instance",
  ]);
  fireEvent.click(button("Form"));
  expect(
    (screen.getByLabelText("Retire from future selection") as HTMLInputElement)
      .checked,
  ).toBe(true);
  fireEvent.click(screen.getByLabelText("Retire from future selection"));
  editor = await yamlEditor();
  expect(readSource(editor.value)?.value.retired).toEqual([]);
});

test("Profile forms retain native permission defaults and unlimited requests", async () => {
  setup(status(source));
  fireEvent.click(await screen.findByRole("button", { name: /^Profiles/ }));
  const policy = screen.getByLabelText(
    "Default tool policy",
  ) as HTMLSelectElement;
  expect(policy.value).toBe("inherit");
  fireEvent.change(policy, { target: { value: "review" } });
  fireEvent.click(screen.getByLabelText("Limit model requests"));
  const editor = await yamlEditor();
  expect(readSource(editor.value)?.value).toHaveProperty(
    "profiles.default.permissions.default",
    "review",
  );
  expect(readSource(editor.value)?.value).toHaveProperty(
    "profiles.default.agent.usage_limits.request_limit",
    null,
  );
  fireEvent.click(button("Form"));
  expect(
    (screen.getByLabelText("Limit model requests") as HTMLInputElement).checked,
  ).toBe(false);
});
