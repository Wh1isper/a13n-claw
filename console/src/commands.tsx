import { createContext, useContext, useState, type ReactNode } from "react";
import { ApiError, message } from "./api";

type Command = {
  payload: unknown;
  busy: boolean;
  error: string;
  result?: unknown;
};
type Commands = {
  entries: Map<string, Command>;
  changed: () => void;
};
const Context = createContext<Commands | null>(null);

// Owned by the authenticated tab, not by a form's mount lifetime. Files remain
// in memory; neither commands nor credentials are written to browser storage.
export function CommandProvider({ children }: { children: ReactNode }) {
  const [entries] = useState(() => new Map<string, Command>());
  const [, render] = useState(0);
  return (
    <Context.Provider value={{ entries, changed: () => render((n) => n + 1) }}>
      {children}
    </Context.Provider>
  );
}

export function usePendingCommands() {
  const store = useContext(Context)!;
  return [...store.entries.values()].some(
    (entry) => entry.result === undefined,
  );
}

export function useCommand<P, R>(key: string) {
  const store = useContext(Context)!;
  const entry = store.entries.get(key);
  // Results are retained even if a form disappears during an accepted command.
  // The next mount can open the saved result instead of creating new work.
  async function execute(
    payload: () => P,
    send: (value: P, retry: boolean) => Promise<R>,
  ) {
    const prior = store.entries.get(key);
    if (prior?.busy) return undefined;
    if (prior?.result !== undefined) return prior.result as R;
    const current: Command = {
      payload: prior?.payload ?? payload(),
      busy: true,
      error: "",
    };
    store.entries.set(key, current);
    store.changed();
    try {
      const result = await send(current.payload as P, !!prior);
      store.entries.set(key, { ...current, busy: false, result });
      return result;
    } catch (reason) {
      // Only a first, explicitly rejected admission proves no work was accepted.
      // A retry/query rejection says nothing about the original attempt.
      if (
        !prior &&
        reason instanceof ApiError &&
        reason.status >= 400 &&
        reason.status < 500
      )
        store.entries.delete(key);
      else
        store.entries.set(key, {
          ...current,
          busy: false,
          error: message(reason),
        });
      throw reason;
    } finally {
      store.changed();
    }
  }
  return {
    payload: entry?.payload as P | undefined,
    result: entry?.result as R | undefined,
    busy: entry?.busy ?? false,
    error: entry?.error ?? "",
    execute,
    clear: () => {
      if (!store.entries.get(key)?.busy) {
        store.entries.delete(key);
        store.changed();
      }
    },
  };
}
