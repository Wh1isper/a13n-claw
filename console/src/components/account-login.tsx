import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { data, type Schema } from "../api/client";
import { useApi } from "../api/session";
import { Badge, Copy, ErrorNotice, Field, Loading, Modal } from "./ui";
import { Icon } from "./icons";

export const loginPending = (
  status: Schema["LoginStatus"] | null | undefined,
) => status?.state === "starting" || status?.state === "waiting";
// Keep observing authorization across dialog closure and route changes.
export function LoginMonitor() {
  const api = useApi();
  const cache = useQueryClient();
  const previous = useRef<Schema["LoginStatus"] | null>(null);
  const active = useQuery({
    queryKey: ["active-login"],
    queryFn: ({ signal }) => data(api.GET("/api/login", { signal })),
    refetchInterval: (query) =>
      loginPending(query.state.data) ? 2_000 : false,
  });
  useEffect(() => {
    if (active.data === undefined) return;
    if (loginPending(previous.current) && !loginPending(active.data)) {
      for (const key of ["account", "subscriptions", "account-candidates"])
        void cache.invalidateQueries({ queryKey: [key] });
      cache.removeQueries({ queryKey: ["copilot-usage"] });
    }
    previous.current = active.data;
  }, [active.data, cache]);
  return null;
}

export function LoginFlow({
  connection,
  accountId,
  active,
  close,
}: {
  connection: Schema["AccountConnection"];
  accountId?: string;
  active?: Schema["LoginStatus"];
  close: () => void;
}) {
  const api = useApi();
  const cache = useQueryClient();
  const [method, setMethod] = useState<Schema["LoginRequest"]["method"]>(
    connection.login_methods.includes("device")
      ? "device"
      : connection.login_methods.includes("manual_callback")
        ? "manual_callback"
        : connection.login_methods[0],
  );
  const [session, setSession] = useState(active ?? null);
  const [callback, setCallback] = useState("");
  const [allowSwitch, setAllowSwitch] = useState(false);
  const status = useQuery({
    queryKey: ["login", session?.session_id],
    enabled: !!session,
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/login/{session_id}", {
          signal,
          params: { path: { session_id: session!.session_id } },
        }),
      ),
    initialData: session ?? undefined,
    refetchInterval: (query) =>
      loginPending(query.state.data) ? 2_000 : false,
  });
  const current = status.data ?? session;
  useEffect(() => {
    if (current && !loginPending(current)) {
      for (const key of [
        "account",
        "subscriptions",
        "account-candidates",
        "active-login",
      ])
        void cache.invalidateQueries({ queryKey: [key] });
      cache.removeQueries({ queryKey: ["copilot-usage"] });
    }
  }, [cache, current?.state, current?.session_id]);
  const start = useMutation({
    mutationFn: () =>
      data(
        api.POST("/api/login", {
          body: {
            provider: connection.provider,
            method,
            account_id: accountId,
            allow_account_switch: allowSwitch,
          },
        }),
      ),
    onSuccess: (value) => {
      setSession(value);
      cache.setQueryData(["active-login"], value);
      void cache.invalidateQueries({ queryKey: ["active-login"] });
    },
  });
  const recover = useMutation({
    mutationFn: () => data(api.GET("/api/login")),
    onSuccess: (value) => {
      if (value) setSession(value);
      else {
        start.reset();
        void cache.invalidateQueries({ queryKey: ["account"] });
        void cache.invalidateQueries({ queryKey: ["subscriptions"] });
      }
    },
  });
  const submit = useMutation({
    mutationFn: () =>
      data(
        api.POST("/api/login/{session_id}/callback", {
          params: { path: { session_id: session!.session_id } },
          body: { callback_url: callback },
        }),
      ),
    onSuccess: (value) => {
      setCallback("");
      cache.setQueryData(["login", value.session_id], value);
    },
  });
  const cancel = useMutation({
    mutationFn: () =>
      data(
        api.POST("/api/login/{session_id}/cancel", {
          params: { path: { session_id: session!.session_id } },
        }),
      ),
    onSuccess: (value) =>
      cache.setQueryData(["login", value.session_id], value),
  });
  return (
    <Modal title={`Connect ${connection.label}`} close={close}>
      <div className="form">
        {!session ? (
          <>
            <Field label="Sign-in method">
              <select
                value={method}
                onChange={(e) =>
                  setMethod(e.target.value as Schema["LoginRequest"]["method"])
                }
              >
                {connection.login_methods.map((m) => (
                  <option key={m} value={m}>
                    {m.replaceAll("_", " ")}
                  </option>
                ))}
              </select>
            </Field>
            <p className="hint">
              {method === "browser"
                ? "Requires a browser on the Claw server."
                : method === "manual_callback"
                  ? "Paste the callback URL after signing in."
                  : "Enter the device code on the verification page."}
            </p>
            {!accountId && (
              <label className="check">
                <input
                  type="checkbox"
                  checked={allowSwitch}
                  onChange={(e) => setAllowSwitch(e.target.checked)}
                />
                Replace selected account
              </label>
            )}
            <ErrorNotice error={start.error} />
            {start.error ? (
              <>
                <button
                  onClick={() => recover.mutate()}
                  disabled={recover.isPending}
                >
                  Check existing authorization
                </button>
                <ErrorNotice error={recover.error} />
              </>
            ) : (
              <button
                className="primary"
                disabled={start.isPending}
                onClick={() => start.mutate()}
              >
                {start.isPending ? "Starting…" : "Start authorization"}
              </button>
            )}
          </>
        ) : (
          <>
            <Badge>{current?.state ?? "starting"}</Badge>
            <p>{current?.message}</p>
            {loginPending(current) && (
              <>
                {current?.verification_url ? (
                  <a
                    className="button primary"
                    target="_blank"
                    rel="noreferrer"
                    href={current.verification_url}
                  >
                    Open verification page <Icon name="external" />
                  </a>
                ) : (
                  <Loading />
                )}
                {current?.user_code && <Copy value={current.user_code} />}
                <p className="hint">
                  Expires after {Math.ceil((current?.expires_in ?? 900) / 60)}{" "}
                  minutes.
                </p>
                {current?.method === "manual_callback" && (
                  <form
                    className="form"
                    onSubmit={(e) => {
                      e.preventDefault();
                      submit.mutate();
                    }}
                  >
                    <Field label="Callback URL">
                      <input
                        type="password"
                        autoComplete="off"
                        required
                        value={callback}
                        onChange={(e) => setCallback(e.target.value)}
                      />
                    </Field>
                    <button disabled={submit.isPending}>Submit callback</button>
                    <ErrorNotice error={submit.error} />
                  </form>
                )}
                <p className="hint">
                  Authorization continues when you close this dialog.
                </p>
                <button
                  className="danger"
                  onClick={() => cancel.mutate()}
                  disabled={cancel.isPending}
                >
                  Cancel authorization
                </button>
              </>
            )}
            <ErrorNotice
              error={status.error}
              retry={() => void status.refetch()}
            />
            <ErrorNotice error={cancel.error} />
            {current?.error_code && (
              <p className="notice error">
                {current.error_code.replaceAll("_", " ")}
              </p>
            )}
            {current && !loginPending(current) && (
              <button className="primary" onClick={close}>
                Done
              </button>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}
