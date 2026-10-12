import { useState } from "react";
import { Api, useRemote, type Principal } from "./api";
import { Action, ErrorNotice, JsonDetails, Status } from "./components";
import { ConfigurationEditor } from "./configuration-editor";

const split = (text: string) =>
  text
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);

export function Settings({
  api,
  changed,
  active,
}: {
  api: Api;
  changed: () => void;
  active: boolean;
}) {
  const [tab, setTab] = useState("Configuration");
  return (
    <div className="settings">
      <header className="page-heading">
        <div>
          <p className="eyebrow">INSTANCE SETTINGS</p>
          <h1>Configure your Claw</h1>
          <p>
            One file for behavior. Protected credentials and explicit access for
            the people and services using it.
          </p>
        </div>
      </header>
      <div className="settings-tabs" role="group" aria-label="Settings area">
        {["Configuration", "Credentials", "Access"].map((item) => (
          <button
            key={item}
            aria-pressed={tab === item}
            onClick={() => setTab(item)}
          >
            {item}
          </button>
        ))}
      </div>
      <div hidden={tab !== "Configuration"}>
        <ConfigurationEditor
          api={api}
          active={active && tab === "Configuration"}
          changed={changed}
        />
      </div>
      <div hidden={tab !== "Credentials"}>
        <Credentials api={api} active={active && tab === "Credentials"} />
      </div>
      <div hidden={tab !== "Access"}>
        <Clients api={api} active={active && tab === "Access"} />
      </div>
    </div>
  );
}
function Credentials({ api, active }: { api: Api; active: boolean }) {
  const saved = useRemote<{ name: string; updated_at: string }[]>(
    api,
    "/credentials",
    0,
    active,
  );
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  return (
    <section className="panel">
      <h2>Credentials</h2>
      <p>
        Backend-owned values override environment variables. The protected
        database and its backups contain secrets. Values cannot be read back.
      </p>
      <ErrorNotice>{saved.error}</ErrorNotice>
      <div className="file-list">
        {saved.data?.map((item) => (
          <div className="file-row" key={item.name}>
            <span>
              <strong>{item.name}</strong>
              <small>
                Updated {new Date(item.updated_at).toLocaleString()}
              </small>
            </span>
            <Action
              danger
              run={async () => {
                if (
                  !confirm(
                    `Remove managed ${item.name}? A process environment value may still resolve this reference.`,
                  )
                )
                  return;
                await api.send(
                  `/credentials/${item.name}`,
                  { value: null },
                  "PUT",
                );
                saved.reload();
              }}
            >
              Remove
            </Action>
          </div>
        ))}
      </div>
      <div className="form-panel">
        <label>
          Reference name
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="OPENAI_API_KEY"
            autoComplete="off"
          />
        </label>
        <label>
          New secret value
          <input
            type="password"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            autoComplete="new-password"
          />
        </label>
        <Action
          disabled={!name || !value}
          run={async () => {
            await api.send(
              `/credentials/${encodeURIComponent(name)}`,
              { value },
              "PUT",
            );
            setValue("");
            saved.reload();
          }}
        >
          Save credential
        </Action>
      </div>
    </section>
  );
}
function Clients({ api, active }: { api: Api; active: boolean }) {
  const clients = useRemote<Principal[]>(api, "/clients", 0, active);
  const [edit, setEdit] = useState<Principal | null>(null);
  const [creating, setCreating] = useState(false);
  const [token, setToken] = useState("");
  return (
    <section className="panel">
      <div className="section-heading">
        <h2>Access</h2>
        <button
          onClick={() => {
            setEdit(null);
            setCreating(true);
            setToken("");
          }}
        >
          New client
        </button>
      </div>
      <p>
        Clients receive explicit actions, Thread scopes and profile selections.
        Granting a profile can expose the shared workspace to that participant's
        agent.
      </p>
      <ErrorNotice>{clients.error}</ErrorNotice>
      <div className="resource-grid">
        {clients.data?.map((client) => (
          <button
            key={client.id}
            className="resource-card"
            disabled={client.id === "operator"}
            onClick={() => {
              setEdit(client);
              setCreating(false);
              setToken("");
            }}
          >
            <strong>{client.id}</strong>
            <span>
              {client.admin ? "Administrator" : client.actions.join(", ")}
            </span>
            <Status value={client.enabled ? "enabled" : "disabled"} />
          </button>
        ))}
      </div>
      {(edit || creating) && (
        <ClientEditor
          key={`${edit?.id ?? "new"}:${edit?.version ?? 0}`}
          api={api}
          initial={edit}
          done={(value) => {
            setToken(value ?? "");
            setEdit(null);
            setCreating(false);
            clients.reload();
          }}
        />
      )}
      {token && (
        <div className="notice">
          <div>
            <strong>New client token — shown once</strong>
            <p>Store it securely. It is not persisted in the browser.</p>
            <code className="secret">{token}</code>
            <button onClick={() => setToken("")}>Dismiss token</button>
          </div>
        </div>
      )}
      <p className="subtle">
        To rotate or recover operator access, stop the server and run{" "}
        <code>a13n-claw reset-operator</code> locally.
      </p>
    </section>
  );
}
function ClientEditor({
  api,
  initial,
  done,
}: {
  api: Api;
  initial: Principal | null;
  done: (token?: string) => void;
}) {
  const [actor, setActor] = useState<Principal>(
    initial ?? {
      id: "",
      version: 1,
      admin: false,
      enabled: true,
      actions: ["read", "submit"],
      thread_ids: [],
      profile_ids: [],
    },
  );
  return (
    <div className="form-panel">
      <label>
        Client ID
        <input
          disabled={!!initial}
          value={actor.id}
          pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"
          maxLength={128}
          onChange={(event) => setActor({ ...actor, id: event.target.value })}
        />
      </label>
      <label className="check-label">
        <input
          type="checkbox"
          checked={actor.enabled}
          onChange={(event) =>
            setActor({ ...actor, enabled: event.target.checked })
          }
        />
        Enabled
      </label>
      <label className="check-label">
        <input
          type="checkbox"
          checked={actor.admin}
          onChange={(event) =>
            setActor({ ...actor, admin: event.target.checked })
          }
        />
        Administrator (all scopes and actions)
      </label>
      <div className="toolbar">
        {["read", "submit", "cancel", "decide", "create"].map((action) => (
          <label className="check-label" key={action}>
            <input
              type="checkbox"
              checked={actor.actions.includes(action)}
              onChange={(event) =>
                setActor({
                  ...actor,
                  actions: event.target.checked
                    ? [...actor.actions, action]
                    : actor.actions.filter((value) => value !== action),
                })
              }
            />
            {action}
          </label>
        ))}
      </div>
      <label>
        Thread IDs (comma-separated)
        <input
          defaultValue={actor.thread_ids.join(", ")}
          onChange={(event) =>
            setActor({ ...actor, thread_ids: split(event.target.value) })
          }
        />
      </label>
      <label>
        Profile IDs (comma-separated)
        <input
          defaultValue={actor.profile_ids.join(", ")}
          onChange={(event) =>
            setActor({ ...actor, profile_ids: split(event.target.value) })
          }
        />
      </label>
      <JsonDetails title="Review grants" value={actor} />
      <Action
        disabled={!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(actor.id)}
        run={async () => {
          if (initial) {
            await api.send(
              `/clients/${encodeURIComponent(actor.id)}`,
              { principal: actor },
              "PUT",
            );
            done();
          } else {
            const result = await api.send<{ token: string }>("/clients", {
              principal: actor,
            });
            done(result.token);
          }
        }}
      >
        Save client
      </Action>
      <button onClick={() => done()}>Cancel</button>
    </div>
  );
}
