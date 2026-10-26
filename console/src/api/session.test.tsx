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
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { Session, useApi, useLogout } from "./session";
import type { Api } from "./client";

const sessions: { api: Api; cache: QueryClient }[] = [];
function Probe() {
  const api = useApi();
  const cache = useQueryClient();
  const logout = useLogout();
  if (!sessions.some((session) => session.api === api))
    sessions.push({ api, cache });
  return <button onClick={logout}>Disconnect</button>;
}
async function login(token: string) {
  fireEvent.change(screen.getByLabelText("Operator token"), {
    target: { value: token },
  });
  fireEvent.click(screen.getByRole("button", { name: "Open Console" }));
  await screen.findByRole("button", { name: "Disconnect" });
}
beforeEach(() => {
  sessions.length = 0;
  const NativeRequest = globalThis.Request;
  vi.stubGlobal(
    "Request",
    class extends NativeRequest {
      constructor(input: RequestInfo | URL, init?: RequestInit) {
        super(
          typeof input === "string"
            ? new URL(input, "http://localhost")
            : input,
          init,
        );
      }
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("clears private cache on disconnect and ignores a late 401 from a previous connection using the same token", async () => {
  let finishOld!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      if (new URL(request.url).pathname === "/api/subscriptions")
        return new Promise<Response>((resolve) => {
          finishOld = resolve;
        });
      return Promise.resolve(Response.json({ items: [] }));
    }),
  );
  render(
    <Session>
      <Probe />
    </Session>,
  );
  await login("fixture-token");
  const first = sessions[0];
  first.cache.setQueryData(["private-result"], "saved private value");
  const oldRequest = first.api
    .GET("/api/subscriptions")
    .catch((error: Error) => error);
  await waitFor(() => expect(finishOld).toBeTypeOf("function"));
  fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
  expect(first.cache.getQueryCache().getAll()).toHaveLength(0);
  expect(
    (screen.getByLabelText("Operator token") as HTMLInputElement).value,
  ).toBe("");
  await login("fixture-token");
  expect(sessions[1].cache).not.toBe(first.cache);
  expect(sessions[1].cache.getQueryData(["private-result"])).toBeUndefined();
  await act(async () => {
    finishOld(Response.json({}, { status: 401 }));
    await oldRequest;
  });
  expect(screen.getByRole("button", { name: "Disconnect" })).toBeTruthy();
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
});
it("rejects invalid login, accepts a corrected token, and disconnects on the current connection's 401", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json(
        { error: { message: "Invalid operator token" } },
        { status: 401 },
      ),
    )
    .mockResolvedValueOnce(Response.json({ items: [] }))
    .mockResolvedValueOnce(Response.json({}, { status: 401 }));
  vi.stubGlobal("fetch", fetch);
  render(
    <Session>
      <Probe />
    </Session>,
  );
  fireEvent.change(screen.getByLabelText("Operator token"), {
    target: { value: "invalid" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Open Console" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Invalid operator token",
  );
  await login("corrected");
  await act(async () => {
    await sessions[0].api.GET("/api/models").catch(() => {});
  });
  expect(
    (screen.getByLabelText("Operator token") as HTMLInputElement).value,
  ).toBe("");
});
