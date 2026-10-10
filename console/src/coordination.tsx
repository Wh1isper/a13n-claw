import { useState } from "react";
import {
  Api,
  allowed,
  message,
  pretty,
  useRemote,
  type Principal,
  type Resource,
  type Thread,
} from "./api";
import { Action, Empty, ErrorNotice, JsonDetails, Status } from "./components";
import { useCommand } from "./commands";

export type CoordinationState = {
  mode: string;
  main_id: string | null;
  paused: boolean;
  version: number;
  failures: number;
  retry_at: string | null;
  blocked_reason: string | null;
  counts: Record<string, number>;
  workers: Thread[];
};
export type InboxItem = {
  id: string;
  sequence: number;
  kind: string;
  disposition: string;
  version: number;
  channel_id: string | null;
  source_thread_id?: string | null;
  source_run_id?: string | null;
  payload: Record<string, unknown> | null;
  note: string;
  dependency_run_id: string | null;
  due_at: string | null;
  human_key: string | null;
  created_at?: string;
  access?: string;
};
type Delivery = {
  id: string;
  thread_id: string;
  channel_id: string;
  text: string;
  status: string;
  version: number;
  error: string | null;
  transport_receipt: string | null;
};
type Channel = {
  id: string;
  version: number;
  active: boolean;
  policy: Record<string, unknown>;
};
type Control = { expected_version: number; paused: boolean; retry: boolean };

export function CoordinationPage({
  api,
  actor,
  profiles,
  active,
  navigate,
}: {
  api: Api;
  actor: Principal;
  profiles: Resource[];
  active: boolean;
  navigate: (id: string) => void;
}) {
  const state = useRemote<CoordinationState>(
    api,
    "/coordination",
    1500,
    active,
  );
  const [profile, setProfile] = useState("");
  const [tab, setTab] = useState("Inbox");
  const [filter, setFilter] = useState("pending");
  const [after, setAfter] = useState(0);
  const data = state.data;
  const inbox = useRemote<InboxItem[]>(
    api,
    data?.main_id
      ? `/inbox?after=${after}${filter ? `&disposition=${filter}` : ""}`
      : null,
    1500,
    active && tab === "Inbox",
  );
  const deliveries = useRemote<Delivery[]>(
    api,
    "/deliveries",
    1500,
    active && tab === "Deliveries",
  );
  const channels = useRemote<Channel[]>(
    api,
    actor.admin ? "/channels" : null,
    3000,
    active && tab === "Channels",
  );
  const [editing, setEditing] = useState<InboxItem | null>(null);
  const [delivery, setDelivery] = useState<Delivery | null>(null);
  const [channel, setChannel] = useState<Channel | null>(null);
  const control = useCommand<Control, CoordinationState>(
    "coordination-control",
  );
  const [error, setError] = useState("");
  const refresh = () => {
    state.reload();
    inbox.reload();
    deliveries.reload();
    channels.reload();
  };
  async function processing(paused: boolean, retry = false) {
    if (!data) return;
    setError("");
    try {
      await control.execute(
        () => ({ expected_version: data.version, paused, retry }),
        async (body, repeated) => {
          if (repeated && !body.retry) {
            const current = await api.get<CoordinationState>("/coordination");
            if (
              current.version > body.expected_version &&
              current.paused === body.paused
            )
              return current;
          }
          return api.send<CoordinationState>(
            "/coordination/control",
            body,
            "PUT",
          );
        },
      );
      control.clear();
      refresh();
    } catch (reason) {
      setError(message(reason));
    }
  }
  return (
    <div className="coordination-page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">DURABLE ATTENTION · EXPLICIT DELIVERY</p>
          <h1>One place to coordinate.</h1>
          <p>
            Saved obligations, independent workers, and no automatic broadcast.
          </p>
        </div>
        <button onClick={refresh}>Refresh saved state</button>
      </header>
      <ErrorNotice>{state.error || error || control.error}</ErrorNotice>
      {!data ? (
        <p className="subtle">Loading coordination…</p>
      ) : data.mode !== "one_thread" ? (
        <section className="panel">
          <h2>Per-Channel mode</h2>
          <p>
            External conversations advance their own Threads. To use one Main
            coordinator, restart with <code>--mode one_thread</code>. Settle
            active work first. Previous mode state remains inactive, not
            migrated.
          </p>
        </section>
      ) : !data.main_id ? (
        <section className="panel setup">
          <h2>Choose Main's starting profile</h2>
          <p>
            There is one persistent Main Thread. It coordinates workers and
            selectively reads the Inbox. Configure a model and profile in
            Settings first.
          </p>
          {actor.admin && (
            <div className="toolbar">
              <select
                aria-label="Main profile"
                value={profile}
                onChange={(e) => setProfile(e.target.value)}
              >
                <option value="">Select a profile</option>
                {profiles.map((p) => (
                  <option key={p.id}>{p.id}</option>
                ))}
              </select>
              <Action
                disabled={!profile}
                run={async () => {
                  await api.send("/coordination/main", { profile_id: profile });
                  refresh();
                }}
              >
                Initialize Main
              </Action>
            </div>
          )}
        </section>
      ) : (
        <>
          <section className="coordination-summary">
            <div className="panel main-control">
              <p className="eyebrow">CANONICAL MAIN</p>
              <div className="section-heading">
                <h2>
                  {data.paused
                    ? "Automatic processing paused"
                    : "Automatic processing enabled"}
                </h2>
                <Status value={data.blocked_reason || "ready"} />
              </div>
              <p>
                Stopping one Run is not a durable pause. Pausing keeps accepted
                work and does not stop workers.
              </p>
              {data.blocked_reason && (
                <p className="notice">
                  Blocked:{" "}
                  <strong>{data.blocked_reason.replaceAll("_", " ")}</strong>.
                  Open Main to inspect decisions and recovery. Unknown sends are
                  reconciled under Deliveries.
                </p>
              )}
              {data.retry_at && (
                <p className="subtle">
                  Next automatic check:{" "}
                  {new Date(data.retry_at).toLocaleString()} · {data.failures}{" "}
                  non-progressing attempt(s)
                </p>
              )}
              <div className="toolbar">
                <button
                  className="primary"
                  onClick={() => navigate(data.main_id!)}
                >
                  Open Main conversation
                </button>
                {allowed(actor, "submit") && (
                  <>
                    <button
                      disabled={control.busy}
                      onClick={() => void processing(!data.paused)}
                    >
                      {control.payload
                        ? "Reconcile pending control"
                        : data.paused
                          ? "Resume automatic processing"
                          : "Pause automatic processing"}
                    </button>
                    {!!data.failures && !control.payload && (
                      <button
                        onClick={() => void processing(data.paused, true)}
                      >
                        Retry pending attention now
                      </button>
                    )}
                    {control.payload && (
                      <button
                        disabled={control.busy}
                        onClick={() => {
                          control.clear();
                          refresh();
                          setError("");
                        }}
                      >
                        Dismiss pending control and refresh
                      </button>
                    )}
                  </>
                )}
              </div>
            </div>
            <div className="attention-counts">
              {["pending", "deferred", "handled", "ignored"].map((key) => (
                <button
                  key={key}
                  onClick={() => {
                    setTab("Inbox");
                    setFilter(key);
                    setAfter(0);
                  }}
                >
                  <strong>{data.counts[key] ?? 0}</strong>
                  <span>{key}</span>
                </button>
              ))}
            </div>
          </section>
          <section className="panel">
            <div className="section-heading">
              <h2>Persistent workers</h2>
              <small>{data.workers.length} owned Threads</small>
            </div>
            <p className="subtle">
              Open any worker to inspect outcomes or speak directly. Your input
              is admitted once; Main receives attention to reconcile the change.
            </p>
            <div className="worker-grid">
              {data.workers.map((worker) => (
                <button
                  className="worker-card"
                  key={worker.id}
                  onClick={() => navigate(worker.id)}
                >
                  <strong>{worker.title}</strong>
                  <span>
                    {worker.profile_id} ·{" "}
                    {worker.archived ? "archived" : "independent history"}
                  </span>
                  <small>Open conversation →</small>
                </button>
              ))}
            </div>
            {!data.workers.length && (
              <p>No workers yet. Ask Main to create one for bounded work.</p>
            )}
          </section>
        </>
      )}
      <nav className="coordination-tabs" aria-label="Coordination views">
        {[
          ...(data?.main_id ? ["Inbox"] : []),
          "Deliveries",
          ...(actor.admin ? ["Channels"] : []),
        ].map((name) => (
          <button
            key={name}
            aria-current={tab === name ? "page" : undefined}
            onClick={() => setTab(name)}
          >
            {name}
          </button>
        ))}
      </nav>
      <section hidden={tab !== "Inbox" || !data?.main_id} className="panel">
        <div className="section-heading">
          <div>
            <h2>Inbox & coordination attention</h2>
            <p className="subtle">
              Reading is not handling. Deferred work keeps its reactivation
              condition.
            </p>
          </div>
          <select
            aria-label="Inbox disposition"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setAfter(0);
            }}
          >
            {["pending", "deferred", "handled", "ignored", ""].map((value) => (
              <option key={value} value={value}>
                {value || "All dispositions"}
              </option>
            ))}
          </select>
        </div>
        <ErrorNotice>{inbox.error}</ErrorNotice>
        {editing && (
          <InboxEditor
            key={editing.id}
            api={api}
            snapshot={editing}
            close={() => setEditing(null)}
            done={() => {
              setEditing(null);
              refresh();
            }}
          />
        )}
        {inbox.data?.map((item) => (
          <article className="attention-item" key={item.id}>
            <div className="section-heading">
              <div className="toolbar">
                <Status value={item.disposition} />
                <strong>{item.kind.replaceAll("_", " ")}</strong>
                <small>{item.channel_id || "Internal coordination"}</small>
              </div>
              {allowed(actor, "submit") && (
                <button disabled={!!editing} onClick={() => setEditing(item)}>
                  Review item
                </button>
              )}
            </div>
            {item.payload ? (
              <div className="prose">
                {typeof item.payload.text === "string"
                  ? item.payload.text
                  : typeof item.payload.instruction === "string"
                    ? item.payload.instruction
                    : "Inspect referenced saved work."}
              </div>
            ) : (
              <p className="notice">
                Content access revoked. The processing obligation is still
                retained.
              </p>
            )}
            {item.source_thread_id && (
              <button
                className="text-button"
                onClick={() => navigate(item.source_thread_id!)}
              >
                Inspect source Thread
              </button>
            )}
            {item.note && (
              <p>
                <strong>Processing note:</strong> {item.note}
              </p>
            )}
            {(item.dependency_run_id || item.due_at || item.human_key) && (
              <p className="subtle">
                Reactivation:{" "}
                {item.dependency_run_id ||
                  item.due_at ||
                  `Human resolution: ${item.human_key}`}
              </p>
            )}
            <JsonDetails
              title="Saved identity, origin and references"
              value={item}
            />
          </article>
        ))}
        {inbox.data?.length === 0 && (
          <Empty title="No items in this view.">
            This filter is empty; other dispositions may still contain work.
          </Empty>
        )}
        <div className="toolbar">
          <button disabled={!after} onClick={() => setAfter(0)}>
            First page
          </button>
          <button
            disabled={!inbox.data || inbox.data.length < 100}
            onClick={() => setAfter(inbox.data!.at(-1)!.sequence)}
          >
            Next 100
          </button>
        </div>
      </section>
      <section hidden={tab !== "Deliveries"} className="panel">
        <h2>External deliveries</h2>
        <p className="subtle">
          Delivery, Inbox handling and Run completion are independent. Unknown
          outcomes are never retried automatically.
        </p>
        <ErrorNotice>{deliveries.error}</ErrorNotice>
        {delivery && (
          <DeliveryEditor
            key={delivery.id}
            api={api}
            snapshot={delivery}
            close={() => setDelivery(null)}
            done={() => {
              setDelivery(null);
              refresh();
            }}
          />
        )}
        {deliveries.data?.map((item) => (
          <article className="attention-item" key={item.id}>
            <div className="section-heading">
              <div className="toolbar">
                <Status value={item.status} />
                <strong>{item.channel_id}</strong>
              </div>
              {actor.admin &&
                ["unknown", "blocked", "not_sent"].includes(item.status) && (
                  <button
                    disabled={!!delivery}
                    onClick={() => setDelivery(item)}
                  >
                    Reconcile delivery
                  </button>
                )}
            </div>
            <div className="prose">{item.text}</div>
            <p className="subtle">
              {item.transport_receipt ||
                item.error ||
                "Saved intent; waiting for transport"}
            </p>
            <code>{item.id}</code>
          </article>
        ))}
        {deliveries.data?.length === 0 && (
          <Empty title="No external sends.">
            Main's final answer and worker output stay in the Console unless
            Main explicitly sends a message.
          </Empty>
        )}
      </section>
      {actor.admin && (
        <section hidden={tab !== "Channels"} className="panel">
          <div className="section-heading">
            <div>
              <h2>Channel bindings</h2>
              <p className="subtle">
                Generic authenticated HTTP ingress and explicit HTTP egress.
                Vendor adapters are separate integrations.
              </p>
            </div>
            <button
              disabled={!!channel}
              onClick={() =>
                setChannel({
                  id: "",
                  version: 0,
                  active: true,
                  policy: {
                    platform: "http",
                    account: "default",
                    channel: "",
                    ingress_actor_id: actor.id,
                    execution_actor_id: actor.id,
                    allowed_senders: [],
                    enabled: true,
                    inbound: true,
                    outbound: false,
                    group: false,
                    require_addressed: true,
                    shared_context_acknowledged: false,
                    profile_id: null,
                    delivery_url: null,
                    credential_env: null,
                  },
                })
              }
            >
              Add Channel
            </button>
          </div>
          <ErrorNotice>{channels.error}</ErrorNotice>
          {channel && (
            <ChannelEditor
              key={`${channel.id}:${channel.version}`}
              api={api}
              snapshot={channel}
              close={() => setChannel(null)}
              done={() => {
                setChannel(null);
                refresh();
              }}
            />
          )}
          {channels.data?.map((item) => (
            <article className="attention-item" key={item.id}>
              <div className="section-heading">
                <strong>{item.id}</strong>
                <Status
                  value={
                    !item.active
                      ? "inactive"
                      : item.policy.enabled
                        ? "ready"
                        : "disabled"
                  }
                />
                <button
                  disabled={!!channel || !item.active}
                  onClick={() => setChannel(item)}
                >
                  Edit policy
                </button>
              </div>
              <p>
                {String(item.policy.platform)} / {String(item.policy.account)} /{" "}
                {String(item.policy.channel)}
              </p>
              <small>
                Inbound {item.policy.inbound ? "enabled" : "disabled"} ·
                Outbound {item.policy.outbound ? "enabled" : "disabled"}
              </small>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}

export function InboxEditor({
  api,
  snapshot,
  close,
  done,
}: {
  api: Api;
  snapshot: InboxItem;
  close: () => void;
  done: () => void;
}) {
  const [disposition, setDisposition] = useState("handled");
  const [condition, setCondition] = useState("human_key");
  const [dependency, setDependency] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="coordination-editor"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError("");
        try {
          await api.send(
            `/inbox/${snapshot.id}`,
            {
              expected_version: snapshot.version,
              disposition,
              note,
              ...(disposition === "deferred"
                ? {
                    [condition]:
                      condition === "due_at"
                        ? new Date(dependency).toISOString()
                        : dependency,
                  }
                : {}),
            },
            "PUT",
          );
          done();
        } catch (reason) {
          setError(message(reason));
        } finally {
          setBusy(false);
        }
      }}
    >
      <h3>Review {snapshot.kind.replaceAll("_", " ")}</h3>
      <p className="subtle">
        Editing saved version {snapshot.version}. A concurrent change is
        rejected; your note stays intact.
      </p>
      <label>
        Disposition
        <select
          value={disposition}
          onChange={(e) => setDisposition(e.target.value)}
        >
          {["handled", "ignored", "deferred", "pending"].map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
      </label>
      {disposition === "deferred" && (
        <div className="form-grid">
          <label>
            Reactivation condition
            <select
              value={condition}
              onChange={(e) => {
                setCondition(e.target.value);
                setDependency("");
              }}
            >
              <option value="human_key">Explicit human resolution</option>
              <option value="dependency_run_id">Worker Run outcome</option>
              <option value="due_at">Time</option>
            </select>
          </label>
          <label>
            {condition === "due_at"
              ? "Local date and time"
              : condition === "dependency_run_id"
                ? "Worker Run ID"
                : "Human resolution key"}
            <input
              type={condition === "due_at" ? "datetime-local" : "text"}
              value={dependency}
              onChange={(e) => setDependency(e.target.value)}
              required
            />
          </label>
        </div>
      )}
      <label>
        Result or reason
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          required
          rows={3}
        />
      </label>
      <ErrorNotice>{error}</ErrorNotice>
      <div className="toolbar">
        <button className="primary" disabled={busy}>
          Save disposition
        </button>
        <button type="button" disabled={busy} onClick={close}>
          Close editor
        </button>
      </div>
    </form>
  );
}

function DeliveryEditor({
  api,
  snapshot,
  close,
  done,
}: {
  api: Api;
  snapshot: Delivery;
  close: () => void;
  done: () => void;
}) {
  const [outcome, setOutcome] = useState("sent");
  const [retry, setRetry] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="coordination-editor"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError("");
        try {
          await api.send(`/deliveries/${snapshot.id}/reconcile`, {
            expected_version: snapshot.version,
            outcome,
            retry: outcome === "not_sent" && retry,
            note,
          });
          done();
        } catch (reason) {
          setError(message(reason));
        } finally {
          setBusy(false);
        }
      }}
    >
      <h3>Reconcile from transport evidence</h3>
      <p>
        Do not infer failure from a timeout. Confirm the original delivery ID
        with the receiver before allowing another attempt.
      </p>
      <label>
        Confirmed outcome
        <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
          <option value="sent">Confirmed sent</option>
          <option value="not_sent">Confirmed not sent</option>
        </select>
      </label>
      {outcome === "not_sent" && (
        <label className="check-label">
          <input
            type="checkbox"
            checked={retry}
            onChange={(e) => setRetry(e.target.checked)}
          />
          Retry the same delivery identity under current policy
        </label>
      )}
      <label>
        Evidence / reconciliation note
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          required
          rows={3}
        />
      </label>
      <ErrorNotice>{error}</ErrorNotice>
      <div className="toolbar">
        <button className="primary" disabled={busy}>
          Save reconciliation
        </button>
        <button type="button" disabled={busy} onClick={close}>
          Close editor
        </button>
      </div>
    </form>
  );
}

function ChannelEditor({
  api,
  snapshot,
  close,
  done,
}: {
  api: Api;
  snapshot: Channel;
  close: () => void;
  done: () => void;
}) {
  const [id, setId] = useState(snapshot.id);
  const [policy, setPolicy] = useState(pretty(snapshot.policy));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="coordination-editor"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError("");
        try {
          await api.send(
            `/channels/${encodeURIComponent(id)}`,
            { expected_version: snapshot.version, policy: JSON.parse(policy) },
            "PUT",
          );
          done();
        } catch (reason) {
          setError(message(reason));
        } finally {
          setBusy(false);
        }
      }}
    >
      <h3>{snapshot.version ? "Edit Channel policy" : "Bind a Channel"}</h3>
      <p>
        Main shares knowledge across all participating Channels. Set{" "}
        <code>shared_context_acknowledged</code> only for conversations
        authorized to share that context. Ingress clients do not automatically
        get API access to Main history.
      </p>
      <label>
        Binding ID
        <input
          value={id}
          onChange={(e) => setId(e.target.value)}
          disabled={!!snapshot.version}
          required
          pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"
        />
      </label>
      <label>
        Policy JSON
        <textarea
          className="code-editor"
          rows={18}
          value={policy}
          onChange={(e) => setPolicy(e.target.value)}
          required
          spellCheck={false}
        />
      </label>
      <p className="subtle">
        Use managed credential references, never literal credentials. Endpoints
        cannot contain credentials. Existing qualified destinations cannot be
        retargeted.
      </p>
      <ErrorNotice>{error}</ErrorNotice>
      <div className="toolbar">
        <button className="primary" disabled={busy}>
          Save Channel policy
        </button>
        <button type="button" disabled={busy} onClick={close}>
          Close editor
        </button>
      </div>
    </form>
  );
}
