import { Icon, IconButton } from "../components/icons";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, data, type Schema } from "../api/client";
import { useApi } from "../api/session";
import { ManagedAllowance, SelectedAllowance } from "../components/allowances";
import { LoginFlow, loginPending } from "../components/account-login";
import { AccountReset } from "../components/account-reset";
import {
  Badge,
  ConfirmAction,
  Empty,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  Panel,
} from "../components/ui";
import { timestamp } from "../format";

type LoginTarget = {
  connection: Schema["AccountConnection"];
  accountId?: string;
  active?: Schema["LoginStatus"];
};
export function Accounts({ choices }: { choices: Schema["ModelChoices"] }) {
  const api = useApi();
  const cache = useQueryClient();
  const [edit, setEdit] = useState<
    Schema["SubscriptionAccount"] | "new" | null
  >(null);
  const [login, setLogin] = useState<LoginTarget | null>(null);
  const [reset, setReset] = useState<Schema["SubscriptionAccount"] | null>(
    null,
  );
  const [confirm, setConfirm] = useState<{
    title: string;
    description: string;
    action: () => Promise<unknown>;
  } | null>(null);
  const subscriptions = useQuery({
    queryKey: ["subscriptions"],
    queryFn: ({ signal }) => data(api.GET("/api/subscriptions", { signal })),
    refetchInterval: 60_000,
  });
  const active = useQuery({
    queryKey: ["active-login"],
    queryFn: ({ signal }) => data(api.GET("/api/login", { signal })),
    refetchInterval: (q) => (loginPending(q.state.data) ? 2_000 : false),
  });
  const connections = (choices.connections ?? []).flatMap((c) =>
    c.account ? [c.account] : [],
  );
  const connectionFor = (provider: Schema["LoginRequest"]["provider"]) =>
    connections.find((c) => c.provider === provider);
  async function changed() {
    await cache.invalidateQueries({ queryKey: ["subscriptions"] });
  }
  return (
    <div className="stack">
      <div className="toolbar">
        <div className="grow">
          <h2>Subscription accounts</h2>
        </div>
        <button className="primary" onClick={() => setEdit("new")}>
          <Icon name="plus" /> Add account
        </button>
      </div>
      <ErrorNotice
        error={subscriptions.error}
        retry={() => void subscriptions.refetch()}
      />
      <ErrorNotice error={active.error} />
      {active.data && connectionFor(active.data.provider) && (
        <div className="notice toolbar">
          <span className="grow">
            {active.data.provider} authorization is {active.data.state}.
          </span>
          <button
            onClick={() =>
              setLogin({
                connection: connectionFor(active.data!.provider)!,
                active: active.data!,
                accountId: active.data!.account_id ?? undefined,
              })
            }
          >
            Resume authorization
          </button>
        </div>
      )}
      {subscriptions.isPending ? (
        <Loading />
      ) : (
        <div className="cards">
          {subscriptions.data?.accounts.map((view) => (
            <ManagedAllowance
              key={view.account.id}
              view={view}
              actions={
                <div className="stack">
                  <dl className="facts">
                    <dt>Account</dt>
                    <dd>
                      <code>{view.account.id}</code>
                    </dd>
                    <dt>Provider identity</dt>
                    <dd>
                      {view.account.upstream_account_id ?? "Not connected"}
                    </dd>
                    <dt>Credential expiry</dt>
                    <dd>{timestamp(view.authentication.expires_at)}</dd>
                  </dl>
                  {view.authentication.message && (
                    <p className="hint">{view.authentication.message}</p>
                  )}
                  <div className="actions">
                    {connectionFor(view.account.provider) && (
                      <button
                        disabled={!!active.data}
                        onClick={() =>
                          setLogin({
                            connection: connectionFor(view.account.provider)!,
                            accountId: view.account.id,
                          })
                        }
                      >
                        Sign in
                      </button>
                    )}
                    <IconButton
                      icon="edit"
                      label="Edit"
                      onClick={() => setEdit(view.account)}
                    />
                    {view.account.provider === "codex" && (
                      <IconButton
                        icon="refresh"
                        label="Reset vouchers"
                        disabled={!view.authentication.usable}
                        onClick={() => setReset(view.account)}
                      />
                    )}
                    {view.authentication.usable && (
                      <IconButton
                        icon="logout"
                        label="Sign out"
                        onClick={() =>
                          setConfirm({
                            title: `Sign out ${view.account.name}?`,
                            description:
                              "Remove this managed account’s credentials. Its registration and group references remain.",
                            action: async () => {
                              await data(
                                api.POST(
                                  "/api/subscriptions/accounts/{account_id}/logout",
                                  {
                                    params: {
                                      path: { account_id: view.account.id },
                                    },
                                  },
                                ),
                              );
                              await changed();
                            },
                          })
                        }
                      />
                    )}
                    <IconButton
                      icon="trash"
                      label="Delete"
                      className="danger"
                      onClick={() =>
                        setConfirm({
                          title: `Delete ${view.account.name}?`,
                          description:
                            "Retire this account and remove its credentials. Referenced accounts may need to be removed from groups first.",
                          action: async () => {
                            await api.DELETE(
                              "/api/subscriptions/accounts/{account_id}",
                              {
                                params: {
                                  path: { account_id: view.account.id },
                                },
                              },
                            );
                            await changed();
                          },
                        })
                      }
                    />
                  </div>
                </div>
              }
            />
          ))}
          {(["copilot", "chatgpt"] as const).map((provider) => (
            <SelectedAccount
              key={provider}
              provider={provider}
              connection={connectionFor(provider)}
              loginBlocked={!!active.data}
              signIn={(connection) => setLogin({ connection })}
            />
          ))}
        </div>
      )}
      {Object.entries(subscriptions.data?.adoption ?? {}).map(
        ([provider, adoption]) =>
          adoption.message && (
            <p className="notice" key={provider}>
              {provider}: {adoption.message}
            </p>
          ),
      )}
      {edit && (
        <AccountEditor
          account={edit === "new" ? undefined : edit}
          close={() => setEdit(null)}
        />
      )}
      {login && (
        <LoginFlow
          {...login}
          close={() => {
            setLogin(null);
            void active.refetch();
          }}
        />
      )}
      {reset && <AccountReset account={reset} close={() => setReset(null)} />}
      {confirm && <ConfirmAction {...confirm} close={() => setConfirm(null)} />}
    </div>
  );
}

function AccountEditor({
  account,
  close,
}: {
  account?: Schema["SubscriptionAccount"];
  close: () => void;
}) {
  const api = useApi();
  const cache = useQueryClient();
  const [name, setName] = useState(account?.name ?? "");
  const [provider, setProvider] = useState<"codex" | "grok">(
    account?.provider ?? "codex",
  );
  const [enabled, setEnabled] = useState(account?.enabled ?? true);
  const save = useMutation({
    mutationFn: async () =>
      account
        ? data(
            api.PATCH("/api/subscriptions/accounts/{account_id}", {
              params: { path: { account_id: account.id } },
              body: { name, enabled },
            }),
          )
        : data(
            api.POST("/api/subscriptions/accounts", {
              body: { provider, name },
            }),
          ),
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: ["subscriptions"] });
      close();
    },
  });
  const ambiguous =
    !account &&
    save.error &&
    (!(save.error instanceof ApiError) || save.error.status >= 500);
  return (
    <Modal
      title={account ? "Edit account" : "Add subscription account"}
      close={() => {
        if (!save.isPending) close();
      }}
    >
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!save.isPending && !ambiguous) save.mutate();
        }}
      >
        <Field label="Name">
          <input
            required
            value={name}
            maxLength={256}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="Provider">
          <select
            value={provider}
            disabled={!!account}
            onChange={(e) => setProvider(e.target.value as "codex" | "grok")}
          >
            <option value="codex">Codex</option>
            <option value="grok">Grok</option>
          </select>
        </Field>
        {account && (
          <label className="check">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            Enabled for model requests
          </label>
        )}
        <ErrorNotice error={save.error} />
        {ambiguous && (
          <p className="notice">
            Outcome unknown. Refresh the account list before creating another.
          </p>
        )}
        <div className="form-actions">
          <button type="button" disabled={save.isPending} onClick={close}>
            Cancel
          </button>
          <button className="primary" disabled={save.isPending || !!ambiguous}>
            {save.isPending ? "Saving…" : "Save account"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function SelectedAccount({
  provider,
  connection,
  loginBlocked,
  signIn,
}: {
  provider: "chatgpt" | "copilot";
  connection?: Schema["AccountConnection"];
  loginBlocked: boolean;
  signIn: (c: Schema["AccountConnection"]) => void;
}) {
  const api = useApi();
  const cache = useQueryClient();
  const [showSources, setShowSources] = useState(false);
  const [confirmLogout, setConfirmLogout] = useState(false);
  const account = useQuery({
    queryKey: ["account", provider],
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/accounts/{provider}", {
          signal,
          params: { path: { provider } },
        }),
      ),
  });
  const candidates = useQuery({
    queryKey: ["account-candidates", provider],
    enabled: showSources,
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/accounts/{provider}/candidates", {
          signal,
          params: { path: { provider } },
        }),
      ),
  });
  async function changed() {
    await cache.cancelQueries({ queryKey: ["copilot-usage"] });
    cache.removeQueries({ queryKey: ["copilot-usage"] });
    await cache.invalidateQueries({ queryKey: ["account", provider] });
    await cache.invalidateQueries({
      queryKey: ["account-candidates", provider],
    });
  }
  const select = useMutation({
    mutationFn: (selection: Schema["AccountSelection"]) =>
      data(
        api.PUT("/api/accounts/{provider}/selection", {
          params: { path: { provider } },
          body: selection,
        }),
      ),
    onSuccess: changed,
  });
  const discover = useMutation({
    mutationFn: () =>
      data(
        api.POST("/api/accounts/{provider}/models", {
          params: { path: { provider } },
        }),
      ),
  });
  return (
    <SelectedAllowance
      provider={provider}
      actions={
        <div className="stack">
          {account.data && (
            <dl className="facts">
              <dt>Source</dt>
              <dd>
                {account.data.source.replaceAll("_", " ")}
                {account.data.shared_with_cli && " · shared with CLI"}
              </dd>
              <dt>Credential expiry</dt>
              <dd>{timestamp(account.data.expires_at)}</dd>
            </dl>
          )}
          <div className="actions">
            {connection && (
              <button
                disabled={loginBlocked}
                onClick={() => signIn(connection)}
              >
                Sign in
              </button>
            )}
            {connection?.source_selection && (
              <IconButton
                icon="archive"
                label="Account sources"
                aria-expanded={showSources}
                onClick={() => setShowSources(!showSources)}
              />
            )}
            {connection?.model_discovery && (
              <IconButton
                icon="search"
                label="Discover models"
                busy={discover.isPending}
                disabled={!account.data?.usable || discover.isPending}
                onClick={() => discover.mutate()}
              />
            )}
            {account.data?.usable && (
              <IconButton
                icon="logout"
                label="Sign out"
                onClick={() => setConfirmLogout(true)}
              />
            )}
          </div>
          {showSources && (
            <div className="stack">
              <ErrorNotice error={candidates.error} />
              {candidates.isPending ? (
                <Loading />
              ) : !candidates.data?.length ? (
                <Empty title="No account sources found" />
              ) : (
                candidates.data.map((candidate) => (
                  <div
                    className="toolbar"
                    key={`${candidate.selection.source}:${candidate.selection.account_id}`}
                  >
                    <span className="grow">{candidate.label}</span>
                    {candidate.selected ? (
                      <Badge>selected</Badge>
                    ) : (
                      <button
                        disabled={select.isPending}
                        onClick={() => select.mutate(candidate.selection)}
                      >
                        Select
                      </button>
                    )}
                  </div>
                ))
              )}
              <ErrorNotice error={select.error} />
            </div>
          )}
          <ErrorNotice error={discover.error} />
          {discover.data && (
            <details open>
              <summary>{discover.data.length} available models</summary>
              <ul>
                {discover.data.map((m) => (
                  <li key={m.value}>
                    {m.label} <code>{m.value}</code>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {confirmLogout && (
            <ConfirmAction
              title={`Sign out ${provider}?`}
              description={
                account.data?.shared_with_cli
                  ? "This account is shared with the CLI. Signing out also affects that credential source."
                  : "Remove the currently selected account’s credentials."
              }
              action={async () => {
                await data(
                  api.POST("/api/accounts/{provider}/logout", {
                    params: { path: { provider } },
                  }),
                );
                await changed();
              }}
              close={() => setConfirmLogout(false)}
            />
          )}
        </div>
      }
    />
  );
}

export function Credentials() {
  const api = useApi();
  const cache = useQueryClient();
  const [edit, setEdit] = useState<string | null>(null);
  const list = useQuery({
    queryKey: ["credentials"],
    queryFn: ({ signal }) => data(api.GET("/api/credentials", { signal })),
  });
  return (
    <Panel
      title="Stored API keys"
      actions={<button onClick={() => setEdit("")}>Add credential</button>}
    >
      <div className="panel-body stack">
        <ErrorNotice error={list.error} />
        {list.isPending ? (
          <Loading />
        ) : list.data?.length ? (
          list.data.map((credential) => (
            <div className="toolbar" key={credential.credential_ref}>
              <code className="grow">{credential.credential_ref}</code>
              <Badge>stored</Badge>
              <IconButton
                icon="key"
                label="Replace"
                onClick={() => setEdit(credential.credential_ref)}
              />
            </div>
          ))
        ) : (
          <Empty title="No stored API keys" />
        )}
      </div>
      {edit !== null && (
        <CredentialEditor
          reference={edit}
          close={() => {
            setEdit(null);
            void cache.invalidateQueries({ queryKey: ["credentials"] });
          }}
        />
      )}
    </Panel>
  );
}
function CredentialEditor({
  reference,
  close,
}: {
  reference: string;
  close: () => void;
}) {
  const api = useApi();
  const [id, setId] = useState(reference || "key-");
  const [secret, setSecret] = useState("");
  const save = useMutation({
    mutationFn: () =>
      data(
        api.PUT("/api/credentials", {
          body: { credential_ref: id, key: secret },
        }),
      ),
    onSuccess: () => {
      setSecret("");
      close();
    },
  });
  return (
    <Modal
      title={reference ? "Replace API key" : "Store API key"}
      close={() => {
        if (!save.isPending) close();
      }}
    >
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <Field label="Credential reference">
          <input
            required
            disabled={!!reference}
            value={id}
            onChange={(e) => setId(e.target.value)}
          />
        </Field>
        <Field label="API key">
          <input
            required
            type="password"
            autoComplete="off"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
          />
        </Field>
        <ErrorNotice error={save.error} />
        <button className="primary" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Store key"}
        </button>
      </form>
    </Modal>
  );
}
