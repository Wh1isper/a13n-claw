// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { createApi } from "./client";
import { watchThread, useThreadWatch } from "./watch";

const context = vi.hoisted(() => ({
  api: null as ReturnType<typeof createApi> | null,
}));
vi.mock("./session", () => ({ useApi: () => context.api }));
beforeEach(() => {
  const NativeRequest = globalThis.Request;
  // Node's Request lacks the browser's relative-URL resolution.
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
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
it("parses split UTF-8/CRLF frames, ignores replay, and ends refetch without inventing completion", async () => {
  const bytes = new TextEncoder().encode(
    'event: snapshot\r\ndata: {"text":"中文"}\r\n\r\nevent: replay\ndata: {}\n\nevent: observation\ndata: {}\n\nevent: refetch\ndata: {}\n\nevent: observation\n\n',
  );
  const stream = new ReadableStream({
    start(controller) {
      for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
      controller.close();
    },
  });
  const fetch = vi.fn().mockResolvedValue(new Response(stream));
  vi.stubGlobal("fetch", fetch);
  const changed = vi.fn();
  const connected = vi.fn();
  await watchThread(
    createApi("fixture-token", vi.fn()),
    "thread-test",
    new AbortController().signal,
    changed,
    connected,
  );
  expect(connected).toHaveBeenCalledOnce();
  expect(changed).toHaveBeenCalledTimes(2);
  const request = fetch.mock.calls[0][0] as Request;
  expect(request.headers.get("Authorization")).toBe("Bearer fixture-token");
  expect(request.cache).toBe("no-store");
});
it("reconnects on EOF, throttles invalidation and aborts the focused observation on unmount", async () => {
  vi.useFakeTimers();
  const signals: AbortSignal[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      signals.push(request.signal);
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode(
                "event: snapshot\n\nevent: observation\n\nevent: observation\n\n",
              ),
            );
            if (signals.length === 1) controller.close();
            else
              request.signal.addEventListener("abort", () =>
                controller.error(new DOMException("Aborted", "AbortError")),
              );
          },
        }),
      );
    }),
  );
  context.api = createApi("fixture", vi.fn());
  const cache = new QueryClient();
  const invalidations = vi.spyOn(cache, "invalidateQueries");
  const { result, unmount } = renderHook(
    () => useThreadWatch("thread-focused"),
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={cache}>{children}</QueryClientProvider>
      ),
    },
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current).toContain("Reconnecting");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000);
  });
  expect(invalidations).toHaveBeenCalledOnce();
  expect(invalidations).toHaveBeenCalledWith({
    queryKey: ["thread", "thread-focused"],
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(4000);
  });
  expect(signals).toHaveLength(2);
  expect(result.current).toBe("Live");
  unmount();
  expect(signals[1].aborted).toBe(true);
  cache.clear();
});

it("stops automatic reconnect after a conclusive HTTP rejection", async () => {
  vi.useFakeTimers();
  const fetch = vi.fn().mockResolvedValue(Response.json({}, { status: 404 }));
  vi.stubGlobal("fetch", fetch);
  context.api = createApi("fixture", vi.fn());
  const cache = new QueryClient();
  const { result, unmount } = renderHook(() => useThreadWatch("missing"), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={cache}>{children}</QueryClientProvider>
    ),
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20_000);
  });
  expect(result.current).toContain("refresh manually");
  expect(fetch).toHaveBeenCalledOnce();
  unmount();
  cache.clear();
});
it("aborts while hidden and reconnects only when visible", async () => {
  vi.useFakeTimers();
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  const signals: AbortSignal[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      signals.push(request.signal);
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode("event: snapshot\n\n"));
            request.signal.addEventListener("abort", () =>
              controller.error(new DOMException("Aborted", "AbortError")),
            );
          },
        }),
      );
    }),
  );
  context.api = createApi("fixture", vi.fn());
  const cache = new QueryClient();
  const { result, unmount } = renderHook(() => useThreadWatch("focused"), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={cache}>{children}</QueryClientProvider>
    ),
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current).toBe("Live");
  await act(async () => {
    hidden.mockReturnValue(true);
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(20_000);
  });
  expect(signals[0].aborted).toBe(true);
  expect(signals).toHaveLength(1);
  expect(result.current).toContain("Paused");
  await act(async () => {
    hidden.mockReturnValue(false);
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(signals).toHaveLength(2);
  expect(result.current).toBe("Live");
  unmount();
  cache.clear();
});
