import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import { Link, MemoryRouter, Route, Routes } from "react-router";
import { afterEach, expect, test } from "vitest";
import contribution from "../src/index";
import { AppContext } from "./bridge";

const collection = (items = []) => ({ items, next_cursor: null });
const response = (body, status = 200) => Response.json(body, { status });
const claw = {
  id: "alpha",
  name: "Alpha",
  workspace_id: "test",
  worker_models: [],
  environment_id: "env",
};
const other = { ...claw, id: "beta", name: "Beta" };
const project = { environment: "ready", browser: null };
const usage = [
  {
    model_id: "model",
    model: "Fixture",
    usage: {
      requests: 1_200_000,
      input_tokens: 2_400_000_000,
      output_tokens: 0,
      cost: "42000",
      unpriced_requests: 1,
    },
  },
];
const pagePath = (id) => `/workspace/test/apps/a13n.claw/${id}`;
const defaultReply = (path) => {
  if (path.startsWith("/claws?")) return response(collection([claw, other]));
  if (path.endsWith("/usage?days=7")) return response(usage);
  if (path === "/claws/alpha" || path === "/claws/beta") {
    return response({
      claw: path.endsWith("alpha") ? claw : other,
      unread: 0,
      read: 0,
      main: null,
      project,
    });
  }
  return response(collection());
};

async function mount(entry, request = defaultReply, language = "en") {
  const i18n = createInstance();
  await i18n.init({
    lng: language,
    showSupportNotice: false,
    resources: { [language]: { translation: {} } },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  const result = render(
    <I18nextProvider i18n={i18n}>
      <AppContext.Provider
        value={{
          workspaceId: "test",
          queryKey: (...parts) => ["claw", "test", ...parts],
          request,
        }}
      >
        <QueryClientProvider client={cache}>
          <MemoryRouter initialEntries={[entry]}>
            <nav>
              <Link to={pagePath("connectors")}>Connector navigation</Link>
              <Link to={`${pagePath("overview")}?claw=alpha`}>
                Alpha deep link
              </Link>
            </nav>
            <Routes>
              {contribution.settings.map(({ id, component: Page }) => (
                <Route
                  key={id}
                  path="/workspace/test/settings/app.a13n.claw.settings"
                  element={<Page />}
                />
              ))}
              {contribution.pages.map(({ id, component: Page }) => (
                <Route
                  key={id}
                  path={pagePath(id)}
                  element={<Page key={id} />}
                />
              ))}
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </AppContext.Provider>
    </I18nextProvider>,
  );
  return { ...result, cache };
}

afterEach(() => {
  cleanup();
  sessionStorage.clear();
});

test("Claw selection survives sidebar navigation; a deep link overrides it", async () => {
  await mount(pagePath("overview"));
  fireEvent.change(await screen.findByRole("combobox", { name: "Claw" }), {
    target: { value: "beta" },
  });
  expect((await screen.findByRole("combobox", { name: "Claw" })).value).toBe(
    "beta",
  );
  fireEvent.click(screen.getByRole("link", { name: "Connector navigation" }));
  await screen.findByRole("heading", { name: "Connectors" });
  expect(screen.getByRole("combobox", { name: "Claw" }).value).toBe("beta");
  fireEvent.click(screen.getByRole("link", { name: "Alpha deep link" }));
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "Claw" }).value).toBe("alpha"),
  );
});

test("saving a connector invalidates an otherwise fresh connector list", async () => {
  let saved;
  const request = (path, init) => {
    if (path === "/project-resources/account")
      return response(collection([{ id: "account", name: "Intake account" }]));
    if (path === "/claws/alpha/connectors") {
      if (init?.method === "POST") {
        const body = JSON.parse(init.body);
        saved = { ...body, id: "connector", credential_configured: true };
        return response(saved, 201);
      }
      return response(collection(saved ? [saved] : []));
    }
    return defaultReply(path);
  };
  await mount(pagePath("connectors"), request);
  fireEvent.click(await screen.findByRole("button", { name: "New connector" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Name" }), {
    target: { value: "New intake" },
  });
  fireEvent.submit(screen.getByRole("textbox", { name: "Name" }).form);
  expect(await screen.findByRole("link", { name: "New intake" })).toBeTruthy();
});

test("Fresh Schedule retry preserves the key until the payload changes", async () => {
  const submissions = [];
  const request = (path, init) => {
    if (path === "/claws/alpha/fresh-schedules" && init?.method === "POST") {
      submissions.push([
        init.headers["Idempotency-Key"],
        JSON.parse(init.body),
      ]);
      return response(
        { error: { message: "Schedule write unavailable" } },
        503,
      );
    }
    return defaultReply(path);
  };
  await mount(`${pagePath("schedules")}?schedule=new&mode=fresh`, request);
  fireEvent.change(await screen.findByRole("textbox", { name: "Name" }), {
    target: { value: "New schedule" },
  });
  const prompt = screen.getByRole("textbox", { name: "Prompt" });
  fireEvent.change(prompt, { target: { value: "Original task" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Cron expression" }), {
    target: { value: "0 9 * * *" },
  });
  const submit = async () => {
    const expected = submissions.length + 1;
    fireEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    await waitFor(() => {
      expect(submissions).toHaveLength(expected);
      expect(
        screen.getByRole("button", { name: "Save schedule" }).disabled,
      ).toBe(false);
      expect(screen.getByRole("alert").textContent).toBe(
        "Schedule write unavailable",
      );
    });
  };
  await submit();
  await submit();
  fireEvent.change(prompt, { target: { value: "Updated task" } });
  await submit();
  expect(submissions[0][0]).toBeTruthy();
  expect(submissions[1]).toEqual(submissions[0]);
  expect(submissions[2][0]).not.toBe(submissions[0][0]);
  expect(submissions[2][1].prompt).toBe("Updated task");
});

test("Dashboard presents compact usage before deliveries and has no composer", async () => {
  const { container } = await mount(pagePath("overview"));
  expect((await screen.findAllByTitle("1,200,000"))[0].textContent).toBe(
    "1.2M",
  );
  expect(screen.getAllByTitle("2,400,000,000")[0].textContent).toBe("2.4B");
  expect(screen.getByText("Partial pricing")).toBeTruthy();
  const headings = screen
    .getAllByRole("heading", { level: 2 })
    .map((node) => node.textContent);
  expect(headings).toContain("Recent deliveries");
  expect(headings.indexOf("Usage")).toBeLessThan(
    headings.indexOf("Recent deliveries"),
  );
  expect(
    container.querySelector("form, textarea, [contenteditable=true]"),
  ).toBeNull();
});

test.each([
  [
    "stopped",
    "Open Environment",
    "/workspace/test/environments/instances",
    /Resume the same instance/,
  ],
  [
    "missing",
    "Open Project settings",
    "/workspace/test/settings/app.a13n.claw.settings?claw=alpha&section=project",
    /Select a usable Environment/,
  ],
])(
  "Dashboard directs operators to the right recovery for a %s Project Environment",
  async (state, action, href, guidance) => {
    await mount(pagePath("overview"), (path) =>
      path === "/claws/alpha"
        ? response({
            claw,
            unread: 0,
            read: 0,
            main: null,
            project: { ...project, environment: state },
          })
        : defaultReply(path),
    );
    expect(await screen.findByText(guidance)).toBeTruthy();
    expect(
      screen.getByRole("link", { name: action }).getAttribute("href"),
    ).toBe(href);
  },
);

test("Workspace Settings is admin-only and the Dashboard follows Console language", async () => {
  expect(contribution.navigation).toEqual({
    label: { en: "Claw", "zh-CN": "Claw" },
    icon: "robot",
  });
  expect(contribution.pages[0].label).toEqual({
    en: "Overview",
    "zh-CN": "总览",
  });
  expect(contribution.pages.map(({ id }) => id)).toEqual([
    "overview",
    "deliveries",
    "connectors",
    "schedules",
  ]);
  expect(contribution.settings).toMatchObject([
    { id: "settings", scope: "workspace", permission: "admin" },
  ]);
  await mount(pagePath("overview"), defaultReply, "zh-CN");
  expect(await screen.findByRole("heading", { name: "用量" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "查看未读" })).toBeTruthy();
});

test("delivery detail links the exact worker thread, worker run, and source connector", async () => {
  const delivery = {
    id: "d1",
    origin: "worker",
    status: "accepted",
    kind: "worker_terminal",
    item_id: "i1",
    content: "done",
    created_at: "2026-01-01T00:00:00Z",
    read_at: null,
    thread_id: "main-thread",
    session_id: "main-session",
    run_id: "main-run",
    source: {
      thread_id: "worker-thread",
      session_id: "worker-session",
      run_id: "worker-run",
      connector_id: "connector-one",
    },
  };
  await mount(`${pagePath("deliveries")}?claw=alpha&delivery=d1`, (path) =>
    path === "/claws/alpha/deliveries/d1"
      ? response(delivery)
      : defaultReply(path),
  );
  await screen.findByText("Source Worker Run");
  const hrefs = screen
    .getAllByRole("link")
    .map((link) => link.getAttribute("href"));
  expect(hrefs).toContain(
    "/workspace/test/sessions/worker-session/threads/worker-thread",
  );
  expect(hrefs).toContain(
    "/workspace/test/sessions/worker-session/threads/worker-thread/runs/worker-run",
  );
  expect(hrefs).toContain(
    "/workspace/test/sessions/main-session/threads/main-thread/runs/main-run",
  );
  expect(hrefs).toContain(
    "/workspace/test/apps/a13n.claw/connectors?claw=alpha&connector=connector-one",
  );
});

test("origin filter scans later pages and retains matching rows", async () => {
  const paths = [];
  const row = (id, origin) => ({
    id,
    origin,
    status: "accepted",
    preview: id,
    item_id: null,
    kind: null,
    read_at: null,
    thread_id: null,
    run_id: null,
    session_id: null,
    created_at: "2026-01-01T00:00:00Z",
  });
  const request = (path) => {
    paths.push(path);
    if (path.startsWith("/claws/alpha/deliveries?limit=100"))
      return response({ items: [row("first", "lark")], next_cursor: "next" });
    if (path.startsWith("/claws/alpha/deliveries?cursor=next"))
      return response({ items: [row("later", "github")], next_cursor: null });
    return defaultReply(path);
  };
  await mount(`${pagePath("deliveries")}?claw=alpha`, request);
  fireEvent.change(await screen.findByRole("combobox", { name: "Origin" }), {
    target: { value: "github" },
  });
  expect(await screen.findByText("later")).toBeTruthy();
  expect(screen.queryByText("first")).toBeNull();
  expect(
    paths.some(
      (path) => path.includes("cursor=next") && path.includes("limit=100"),
    ),
  ).toBe(true);
});

test("settings keeps draft and immediate project operations in separate sections", async () => {
  const { container } = await mount(
    "/workspace/test/settings/app.a13n.claw.settings?claw=alpha",
  );
  const name = await screen.findByRole("textbox", { name: "Name" });
  fireEvent.change(name, { target: { value: "Draft" } });
  fireEvent.change(
    screen.getByRole("combobox", { name: "Claw settings sections" }),
    { target: { value: "project" } },
  );
  expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull();
  expect(await screen.findByText("Agent revision")).toBeTruthy();
  fireEvent.change(
    screen.getByRole("combobox", { name: "Claw settings sections" }),
    { target: { value: "general" } },
  );
  expect(screen.getByRole("textbox", { name: "Name" }).value).toBe("Draft");
  expect(
    container.querySelector('form [data-field-layout="setting"]'),
  ).toBeTruthy();
});

test("resource picker belongs to a labeled form field", async () => {
  const { container } = await mount(
    "/workspace/test/settings/app.a13n.claw.settings?new=1",
  );
  const picker = await screen.findByRole("combobox", { name: "Agent" });
  expect(picker.closest('[data-slot="field"]')).toBeTruthy();
  expect(
    container.querySelector('form [data-field-layout="setting"]'),
  ).toBeTruthy();
});

test("headless origin filter matches the API input origin", async () => {
  const entry = {
    id: "headless",
    origin: "input",
    status: "accepted",
    preview: "API input",
    item_id: null,
    kind: null,
    read_at: null,
    thread_id: null,
    run_id: null,
    session_id: null,
    created_at: "2026-01-01T00:00:00Z",
  };
  await mount(`${pagePath("deliveries")}?claw=alpha`, (path) =>
    path.startsWith("/claws/alpha/deliveries?limit=100")
      ? response(collection([entry]))
      : defaultReply(path),
  );
  fireEvent.change(await screen.findByRole("combobox", { name: "Origin" }), {
    target: { value: "input" },
  });
  expect(await screen.findByText("API input")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Refresh results" })).toBeTruthy();
});
