import { useState } from "react";
import { Api, pretty, useRemote, type Principal, type Resource } from "./api";
import { Action, ErrorNotice, JsonDetails, Status } from "./components";

const kinds = ["model", "environment", "profile", "skill", "mcp", "defaults"];
const templates: Record<string, Record<string, unknown>> = {
  model: {
    provider: "openai",
    model_name: "",
    credential_env: "OPENAI_API_KEY",
    base_url: null,
  },
  environment: {
    kind: "local",
    files: "read",
    shell: false,
    retention: "keep",
  },
  profile: {
    model_id: "",
    environment_id: "",
    agent: { usage_limits: { request_limit: 50 } },
    permissions: {},
    skills: [],
    mcp_servers: [],
    child_profiles: [],
  },
  skill: { name: "my-skill", description: "", instructions: "", files: {} },
  mcp: { url: "", header_env: {}, allowed_tools: null, description: null },
  defaults: { profile_id: null },
};
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
  const resources = useRemote<Resource[]>(api, "/resources", 0, active);
  const [kind, setKind] = useState("model");
  const [selection, setSelection] = useState<Resource | null>(null);
  const [creating, setCreating] = useState(false);
  const [transfer, setTransfer] = useState("");
  const refresh = () => {
    resources.reload();
    changed();
  };
  return (
    <div className="settings">
      <header className="page-heading">
        <div>
          <p className="eyebrow">INSTANCE CONFIGURATION</p>
          <h1>Settings</h1>
          <p>
            Reusable definitions. Captured Runs keep their accepted versions.
          </p>
        </div>
      </header>
      <ErrorNotice>{resources.error}</ErrorNotice>
      <section className="panel">
        <div className="section-heading">
          <h2>Resources</h2>
          <button
            onClick={() => {
              setCreating(true);
              setSelection(null);
            }}
          >
            New {kind}
          </button>
        </div>
        <div className="tabs" role="group" aria-label="Resource kind">
          {kinds.map((item) => (
            <button
              key={item}
              aria-pressed={kind === item}
              onClick={() => {
                setKind(item);
                setSelection(null);
                setCreating(false);
              }}
            >
              {item}
            </button>
          ))}
        </div>
        <div className="resource-grid">
          {resources.data
            ?.filter((item) => item.kind === kind)
            .map((item) => (
              <button
                className="resource-card"
                key={item.id}
                onClick={() => {
                  setSelection(item);
                  setCreating(false);
                }}
              >
                <strong>{item.id}</strong>
                <span>Version {item.version}</span>
                <Status value={item.retired ? "retired" : "available"} />
              </button>
            ))}
        </div>
        {!resources.data?.some((item) => item.kind === kind) && (
          <p className="subtle">
            No {kind} definitions yet. Create one or import a reviewed resource
            bundle.
          </p>
        )}
        {(creating || selection) && (
          <ResourceEditor
            key={`${kind}:${selection?.id ?? "new"}:${selection?.version ?? 0}`}
            api={api}
            kind={kind}
            resource={selection}
            resources={resources.data ?? []}
            done={() => {
              setCreating(false);
              setSelection(null);
              refresh();
            }}
          />
        )}
        <details>
          <summary>Atomic import / export</summary>
          <p>
            Exports contain credential reference names, never secret values.
            Import uses each resource's expected_version; zero creates a new
            resource. Review collisions instead of overwriting them.
          </p>
          <div className="toolbar">
            <button
              onClick={() =>
                setTransfer(
                  pretty({
                    resources:
                      resources.data?.map(({ version, ...item }) => ({
                        ...item,
                        expected_version: version,
                      })) ?? [],
                  }),
                )
              }
            >
              Prepare export
            </button>
            <Action
              disabled={!transfer.trim()}
              run={async () => {
                await api.send("/resources/import", JSON.parse(transfer));
                refresh();
              }}
            >
              Import reviewed bundle
            </Action>
          </div>
          <label>
            Resource bundle (JSON)
            <textarea
              className="code-editor"
              rows={12}
              value={transfer}
              onChange={(event) => setTransfer(event.target.value)}
            />
          </label>
        </details>
      </section>
      <Credentials api={api} active={active} />
      <Clients api={api} active={active} />
      <p className="subtle">
        Memory, bridges and automation are later delivery packages. They are not
        enabled by these settings.
      </p>
    </div>
  );
}

function ResourceEditor({
  api,
  kind,
  resource,
  resources,
  done,
}: {
  api: Api;
  kind: string;
  resource: Resource | null;
  resources: Resource[];
  done: () => void;
}) {
  const [id, setId] = useState(
    resource?.id ?? (kind === "defaults" ? "instance" : ""),
  );
  const [content, setContent] = useState<Record<string, unknown>>(
    resource?.content ?? templates[kind],
  );
  const [retired, setRetired] = useState(resource?.retired ?? false);
  const [advanced, setAdvanced] = useState<string | null>(null);
  const update = (key: string, value: unknown) =>
    setContent((previous) => ({
      ...previous,
      [key]: value,
      ...(key === "kind" && value === "local"
        ? { image: null, user: null }
        : {}),
    }));
  const field = (label: string, key: string, placeholder = "") => (
    <label>
      {label}
      <input
        value={String(content[key] ?? "")}
        placeholder={placeholder}
        onChange={(event) =>
          update(key, event.target.value || (key === "base_url" ? null : ""))
        }
      />
    </label>
  );
  const select = (label: string, key: string, options: string[]) => (
    <label>
      {label}
      <select
        value={String(content[key] ?? "")}
        onChange={(event) => update(key, event.target.value)}
      >
        <option value="">Select…</option>
        {options.map((value) => (
          <option key={value}>{value}</option>
        ))}
      </select>
    </label>
  );
  const ids = (type: string) =>
    resources
      .filter((item) => item.kind === type && !item.retired)
      .map((item) => item.id);
  return (
    <section className="resource-editor form-panel">
      <h3>
        {resource
          ? `Edit ${resource.id} · version ${resource.version}`
          : `New ${kind}`}
      </h3>
      <label>
        Resource ID
        <input
          required
          disabled={!!resource || kind === "defaults"}
          value={id}
          onChange={(event) => setId(event.target.value)}
          pattern="[A-Za-z0-9][A-Za-z0-9_.-]*"
        />
      </label>
      {advanced === null ? (
        <>
          {kind === "model" && (
            <>
              {select("Provider", "provider", [
                "openai",
                "anthropic",
                "google",
              ])}
              {field("Model name", "model_name")}
              {field("Credential reference", "credential_env")}
              {field("Base URL (optional)", "base_url")}
              <p className="subtle">
                Save the credential value separately below. A configured model
                is not a connectivity check.
              </p>
            </>
          )}
          {kind === "environment" && (
            <>
              {select("Environment provider", "kind", ["local", "docker"])}
              {content.kind === "docker" && (
                <>
                  {field("Docker image", "image", "python:3.13-slim-bookworm")}
                  {field("Container user (optional)", "user")}
                </>
              )}
              {select("File authority", "files", ["none", "read", "write"])}
              {select("Idle retention", "retention", ["keep", "stop"])}
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={content.shell === true}
                  onChange={(event) => update("shell", event.target.checked)}
                />
                Allow shell execution
              </label>
              <p className="notice">
                Local shell runs on the server account. All Threads share one
                workspace; containers do not isolate shared working files.
              </p>
            </>
          )}
          {kind === "profile" && (
            <>
              {select("Model", "model_id", ids("model"))}
              {select("Environment", "environment_id", ids("environment"))}
              <label>
                Skills (comma-separated IDs)
                <input
                  defaultValue={((content.skills as string[]) ?? []).join(", ")}
                  onChange={(event) =>
                    update("skills", split(event.target.value))
                  }
                />
              </label>
              <label>
                MCP servers (comma-separated IDs)
                <input
                  defaultValue={((content.mcp_servers as string[]) ?? []).join(
                    ", ",
                  )}
                  onChange={(event) =>
                    update("mcp_servers", split(event.target.value))
                  }
                />
              </label>
              <label>
                Child profiles (comma-separated IDs)
                <input
                  defaultValue={(
                    (content.child_profiles as string[]) ?? []
                  ).join(", ")}
                  onChange={(event) =>
                    update("child_profiles", split(event.target.value))
                  }
                />
              </label>
              <p className="subtle">
                Set request, token and tool-call limits in agent.usage_limits
                below. The default request_limit is 50.
              </p>
              <JsonField
                label="Agent behavior (native AgentSpec JSON)"
                value={content.agent ?? {}}
                change={(value) => update("agent", value)}
              />
              <JsonField
                label="Tool permissions (native ToolPermissions JSON)"
                value={content.permissions ?? {}}
                change={(value) => update("permissions", value)}
              />
              <p className="subtle">
                Rules match stable permission IDs, not visible function names.
                Claw IDs: <code>claw.files.read</code>,{" "}
                <code>claw.files.retain</code>, <code>claw.work.delegate</code>,{" "}
                <code>claw.work.inspect</code>. For example,{" "}
                <code>{'{"rules":{"claw.files.retain":"ask"}}'}</code> requires
                artifact approval. Use <code>default</code> to set a policy for
                all tools.
              </p>
            </>
          )}
          {kind === "skill" && (
            <>
              {field("Skill name", "name")}
              {field("Description", "description")}
              <label>
                Instructions
                <textarea
                  rows={8}
                  value={String(content.instructions ?? "")}
                  onChange={(event) =>
                    update("instructions", event.target.value)
                  }
                />
              </label>
              <JsonField
                label="Supporting files (relative path to text)"
                value={content.files ?? {}}
                change={(value) => update("files", value)}
              />
            </>
          )}
          {kind === "mcp" && (
            <>
              {field("Streamable HTTP endpoint", "url")}
              {field("Description (optional)", "description")}
              <JsonField
                label="Header names to credential references"
                value={content.header_env ?? {}}
                change={(value) => update("header_env", value)}
              />
              <JsonField
                label="Allowed tools (array, or null for all)"
                value={content.allowed_tools ?? null}
                change={(value) => update("allowed_tools", value)}
              />
            </>
          )}
          {kind === "defaults" &&
            select("Default profile", "profile_id", ids("profile"))}
          <button onClick={() => setAdvanced(pretty(content))}>
            Edit full definition as JSON
          </button>
        </>
      ) : (
        <label>
          Definition (JSON)
          <textarea
            className="code-editor"
            rows={16}
            value={advanced}
            onChange={(event) => setAdvanced(event.target.value)}
          />
        </label>
      )}
      <label className="check-label">
        <input
          type="checkbox"
          checked={retired}
          onChange={(event) => setRetired(event.target.checked)}
        />
        Retire from future selection
      </label>
      <div className="toolbar">
        <Action
          disabled={!id.trim()}
          run={async () => {
            await api.send("/resources/import", {
              resources: [
                {
                  id,
                  kind,
                  expected_version: resource?.version ?? 0,
                  retired,
                  content: advanced !== null ? JSON.parse(advanced) : content,
                },
              ],
            });
            done();
          }}
        >
          Save definition
        </Action>
        <button onClick={done}>Cancel</button>
      </div>
    </section>
  );
}
function JsonField({
  label,
  value,
  change,
}: {
  label: string;
  value: unknown;
  change: (value: unknown) => void;
}) {
  const [text, setText] = useState(pretty(value));
  const [error, setError] = useState("");
  return (
    <label>
      {label}
      <textarea
        className="code-editor"
        rows={4}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          try {
            change(JSON.parse(event.target.value));
            setError("");
          } catch {
            change(event.target.value);
            setError(
              "Invalid JSON. Fix this field before saving; the server will reject it.",
            );
          }
        }}
      />
      <ErrorNotice>{error}</ErrorNotice>
    </label>
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
