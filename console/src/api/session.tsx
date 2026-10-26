import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createApi, data, type Api } from "./client";

const Context = createContext<{ api: Api; logout: () => void } | null>(null);
export function useApi() {
  const value = useContext(Context);
  if (!value) throw new Error("Console session is missing");
  return value.api;
}
export function useLogout() {
  return useContext(Context)!.logout;
}

function Connected({
  token,
  logout,
  children,
}: {
  token: string;
  logout: () => void;
  children: ReactNode;
}) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: false,
            refetchOnWindowFocus: false,
          },
          mutations: { retry: false },
        },
      }),
  );
  const [api] = useState(() => createApi(token, logout));
  useEffect(
    () => () => {
      void queryClient.cancelQueries();
      queryClient.clear();
    },
    [queryClient],
  );
  return (
    <Context value={{ api, logout }}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </Context>
  );
}

export function Session({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<{ token: string } | null>(null);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  if (session)
    return (
      <Connected
        token={session.token}
        logout={() =>
          setSession((current) => (current === session ? null : current))
        }
      >
        {children}
      </Connected>
    );
  return (
    <main className="login">
      <form
        className="login-card"
        onSubmit={async (event) => {
          event.preventDefault();
          setPending(true);
          setError("");
          try {
            await data(createApi(draft.trim(), () => {}).GET("/api/models"));
            setSession({ token: draft.trim() });
            setDraft("");
          } catch (cause) {
            setError(
              cause instanceof Error ? cause.message : "Unable to connect",
            );
          } finally {
            setPending(false);
          }
        }}
      >
        <span className="brand-mark" aria-hidden="true">
          a
        </span>
        <h1>Claw Console</h1>
        <label className="field">
          Operator token
          <input
            type="password"
            autoComplete="off"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            required
            autoFocus
          />
        </label>
        <p className="hint">
          Find your token in <code>operator.token</code>.
        </p>
        {error && (
          <p className="notice error" role="alert">
            {error}
          </p>
        )}
        <button className="primary" disabled={pending || !draft.trim()}>
          {pending ? "Connecting…" : "Open Console"}
        </button>
      </form>
    </main>
  );
}
