import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, type Api } from "./client";
import { useApi } from "./session";

// Only invalidation signals are consumed here. Saved views remain the authority;
// replayed text and an ended transport never imply execution completion.
export async function watchThread(
  api: Api,
  threadId: string,
  signal: AbortSignal,
  changed: () => void,
  connected: () => void,
) {
  const { data: stream } = await api.GET("/api/threads/{thread_id}/events", {
    params: { path: { thread_id: threadId } },
    parseAs: "stream",
    signal,
  });
  if (!stream) throw new Error("Observation stream is unavailable");
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let match: RegExpExecArray | null;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        const event = /^event: ?([^\r\n]+)/m.exec(frame)?.[1];
        if (event === "snapshot") connected();
        if (event === "snapshot" || event === "observation") changed();
        if (event === "refetch") return;
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export function useThreadWatch(threadId: string | undefined) {
  const api = useApi();
  const cache = useQueryClient();
  const [state, setState] = useState("Connecting");
  useEffect(() => {
    if (!threadId) return;
    let controller: AbortController | undefined;
    let refresh: ReturnType<typeof setTimeout> | undefined;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    const invalidate = () => {
      if (refresh) return;
      refresh = setTimeout(() => {
        refresh = undefined;
        void cache.invalidateQueries({ queryKey: ["thread", threadId] });
      }, 1000);
    };
    const connect = async () => {
      if (disposed || document.hidden) return;
      controller = new AbortController();
      const signal = controller.signal;
      setState("Connecting");
      try {
        await watchThread(api, threadId, signal, invalidate, () =>
          setState("Live"),
        );
      } catch (error) {
        if (signal.aborted) return;
        if (error instanceof ApiError && error.status < 500) {
          setState("Observation unavailable · refresh manually");
          invalidate();
          return;
        }
      }
      if (signal.aborted || disposed) return;
      setState("Reconnecting · showing last saved state");
      invalidate();
      reconnect = setTimeout(() => void connect(), 5000);
    };
    const visibility = () => {
      controller?.abort();
      clearTimeout(reconnect);
      if (document.hidden) setState("Paused while tab is hidden");
      else void connect();
    };
    document.addEventListener("visibilitychange", visibility);
    void connect();
    return () => {
      disposed = true;
      controller?.abort();
      clearTimeout(refresh);
      clearTimeout(reconnect);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [api, cache, threadId]);
  return threadId ? state : "Saved view";
}
