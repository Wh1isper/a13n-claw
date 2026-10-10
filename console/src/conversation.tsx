import { useState } from "react";
import {
  Api,
  activeStatuses,
  allowed,
  message,
  pretty,
  requestId,
  useRemote,
  type Asset,
  type Child,
  type Decision,
  type Input,
  type Principal,
  type Resource,
  type Run,
  type Submission,
  type Thread,
} from "./api";
import { Action, Empty, ErrorNotice, JsonDetails, Status } from "./components";
import { useCommand } from "./commands";

export type Draft = {
  text: string;
  files: string[];
  separate: boolean;
  generation: number;
};
export const blankDraft = (): Draft => ({
  generation: 0,
  text: "",
  files: [],
  separate: false,
});

export function Conversation({
  api,
  actor,
  thread,
  profiles,
  draft,
  setDraft,
  changed,
  navigate,
  active,
}: {
  active: boolean;
  api: Api;
  actor: Principal;
  thread: Thread;
  profiles: Resource[];
  draft: Draft;
  setDraft: (update: (value: Draft) => Draft) => void;
  changed: () => void;
  navigate: (id: string) => void;
}) {
  const inputs = useRemote<Input[]>(
    api,
    `/threads/${thread.id}/inputs`,
    1200,
    active,
  );
  const runs = useRemote<Run[]>(
    api,
    `/threads/${thread.id}/runs`,
    1200,
    active,
  );
  const assets = useRemote<Asset[]>(
    api,
    `/threads/${thread.id}/files`,
    2000,
    active,
  );
  const [edit, setEdit] = useState<Thread | null>(null);
  const [history, setHistory] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const refresh = () => {
    inputs.reload();
    runs.reload();
    assets.reload();
    changed();
  };
  return (
    <div className="conversation">
      <header className="conversation-heading">
        <div>
          <p className="eyebrow">THREAD · {thread.profile_id}</p>
          <h1>{thread.title}</h1>
          <small>
            {thread.archived
              ? "Archived · history retained"
              : "Shared workspace · independent conversation"}
          </small>
        </div>
        <div className="toolbar">
          <button onClick={() => setHistory(!history)}>History</button>
          {allowed(actor, "submit") && (
            <button disabled={!!edit} onClick={() => setEdit(thread)}>
              Thread settings
            </button>
          )}
        </div>
      </header>
      {thread.parent_thread_id && (
        <p className="subtle">
          Related to{" "}
          <button
            className="text-button"
            onClick={() => navigate(thread.parent_thread_id!)}
          >
            {thread.parent_thread_id}
          </button>
        </p>
      )}
      <ErrorNotice>{inputs.error || runs.error || assets.error}</ErrorNotice>
      {edit && (
        <ThreadSettings
          api={api}
          snapshot={edit}
          profiles={profiles}
          done={() => {
            setEdit(null);
            refresh();
          }}
        />
      )}
      {history && <SavedHistory api={api} thread={thread} active={active} />}
      <div className="timeline">
        {runs.data?.length === 0 && (
          <Empty title="Start something worth continuing.">
            Send a message to start work. Further messages steer active work or
            wait at a decision boundary. Closing this page does not stop a Run.
          </Empty>
        )}
        {runs.data?.map((run) => (
          <article className="run-block" key={run.id}>
            <div className="run-heading">
              <span>{new Date(run.created_at).toLocaleString()}</span>
              <Status value={run.status} />
            </div>
            {inputs.data
              ?.filter((input) => input.run_id === run.id)
              .map((input) => (
                <div className="message user-message" key={input.id}>
                  <div className="message-label">
                    <strong>{input.actor_id}</strong>
                    <Status value={input.disposition} />
                  </div>
                  <div className="prose">{input.text}</div>
                  {input.attachment_ids.length > 0 && (
                    <small>
                      {input.attachment_ids.length} retained attachment(s)
                    </small>
                  )}
                  {["uncertain", "blocked"].includes(input.disposition) &&
                    actor.admin && (
                      <ReviewInput api={api} input={input} done={refresh} />
                    )}
                </div>
              ))}
            {run.output !== null && (
              <div className="message assistant-message">
                <div className="message-label">
                  <strong>Claw</strong>
                  <small>Saved output</small>
                </div>
                <div className="prose">{run.output}</div>
              </div>
            )}
            {run.error && <div className="notice error">{run.error}</div>}
            {run.status === "running" && (
              <p className="working" role="status">
                Working in {run.composition.environment.id}. The saved outcome
                appears here when published.
              </p>
            )}
            {run.status === "waiting" && (
              <DecisionPanel
                api={api}
                run={run}
                actor={actor}
                done={refresh}
                active={active}
              />
            )}
            <details
              className="run-details"
              onToggle={(event) => {
                const open = event.currentTarget.open;
                setExpanded((previous) => ({ ...previous, [run.id]: open }));
              }}
            >
              <summary>Execution details · {run.id.slice(-8)}</summary>
              <div className="details-body">
                <p>
                  <code>{run.id}</code>
                </p>
                <JsonDetails
                  title="Captured configuration (not next-Run defaults)"
                  value={run.composition}
                />
                <p className="subtle">
                  Checkpoint: {run.checkpoint_id ?? "Not yet published"}
                  {run.recovery_of && ` · Recovery of ${run.recovery_of}`}
                </p>
                <ChildRuns
                  api={api}
                  run={run}
                  navigate={navigate}
                  active={active && !!expanded[run.id]}
                />
                {allowed(actor, "cancel") && activeStatuses.has(run.status) && (
                  <Action
                    danger
                    disabled={run.cancel_requested}
                    run={async () => {
                      await api.send(`/runs/${run.id}/cancel`);
                      refresh();
                    }}
                  >
                    {run.cancel_requested
                      ? "Cancellation requested"
                      : "Cancel this Run"}
                  </Action>
                )}
                {actor.admin &&
                  ["failed", "interrupted", "cancelled"].includes(
                    run.status,
                  ) && <Recovery api={api} run={run} done={refresh} />}
                {allowed(actor, "create") && run.checkpoint_id && (
                  <Fork
                    api={api}
                    run={run}
                    profiles={profiles}
                    done={navigate}
                  />
                )}
              </div>
            </details>
          </article>
        ))}
      </div>
      {inputs.data?.some((input) => input.disposition === "blocked") &&
        actor.admin && (
          <div className="notice">
            <div>
              <strong>Blocked inputs remain saved.</strong>
              <p>
                Review uncertainty and recovery before releasing known unapplied
                work.
              </p>
              <Action
                run={async () => {
                  await api.send(`/threads/${thread.id}/release-inputs`);
                  refresh();
                }}
              >
                Release blocked inputs
              </Action>
            </div>
          </div>
        )}
      {assets.data && assets.data.length > 0 && (
        <details className="panel">
          <summary>Retained files · {assets.data.length}</summary>
          <div className="file-list">
            {assets.data.map((file) => (
              <div key={file.id} className="file-row">
                <span>
                  <strong>{file.name}</strong>
                  <small>
                    {file.kind} · {(file.size / 1024).toFixed(1)} KiB ·{" "}
                    {file.sha256.slice(0, 12)}
                  </small>
                </span>
                <Action run={() => api.download(file)}>Download</Action>
              </div>
            ))}
          </div>
        </details>
      )}
      {allowed(actor, "submit") && !thread.archived && (
        <Composer
          key={thread.id}
          api={api}
          actor={actor}
          thread={thread}
          files={assets.data ?? []}
          draft={draft}
          update={setDraft}
          done={refresh}
        />
      )}
    </div>
  );
}

function Composer({
  api,
  actor,
  thread,
  files,
  draft,
  update,
  done,
}: {
  api: Api;
  actor: Principal;
  thread: Thread;
  files: Asset[];
  draft: Draft;
  update: (change: (value: Draft) => Draft) => void;
  done: () => void;
}) {
  const command = useCommand<Submission, Input>(`input:${thread.id}`);
  const upload = useCommand<
    { request_id: string; file: File; generation: number },
    Asset
  >(`upload:${thread.id}`);
  const busy = command.busy || upload.busy;
  const pending = command.payload;
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState("");
  async function submit() {
    if (busy || upload.payload) return;
    setError("");
    setReceipt("");
    try {
      const accepted = await command.execute(
        () => ({
          request_id: requestId(),
          text: draft.text,
          attachment_ids: [...draft.files],
          separate_run: draft.separate,
        }),
        async (request, retry) => {
          const prior = retry
            ? (await api.get<Input[]>(`/threads/${thread.id}/inputs`)).find(
                (item) =>
                  item.request_id === request.request_id &&
                  item.actor_id === actor.id,
              )
            : undefined;
          return (
            prior ??
            (await api.send<Input>(`/threads/${thread.id}/inputs`, request))
          );
        },
      );
      if (!accepted) return;
      update((current) =>
        current.generation === draft.generation
          ? { ...blankDraft(), generation: current.generation + 1 }
          : current,
      );
      command.clear();
      setReceipt(
        `Input accepted · ${accepted.disposition}. Acceptance is not completion.`,
      );
      done();
    } catch (reason) {
      setError(message(reason));
    }
  }
  async function uploadFile(file: File) {
    setError("");
    try {
      if (file.size > 8 * 1024 * 1024)
        throw new Error("Files are limited to 8 MiB.");
      const saved = await upload.execute(
        () => ({ request_id: requestId(), file, generation: draft.generation }),
        async (request) => {
          const response = await api.raw(
            `/threads/${thread.id}/files?request_id=${request.request_id}&name=${encodeURIComponent(request.file.name)}`,
            {
              method: "POST",
              headers: {
                "Content-Type": request.file.type || "application/octet-stream",
              },
              body: request.file,
            },
          );
          return response.json();
        },
      );
      if (!saved) return;
      update((current) =>
        current.generation === draft.generation
          ? { ...current, files: [...new Set([...current.files, saved.id])] }
          : current,
      );
      upload.clear();
      done();
    } catch (reason) {
      setError(message(reason));
    }
  }
  return (
    <section className="composer" aria-label="Message composer">
      <ErrorNotice>{error || command.error || upload.error}</ErrorNotice>
      {receipt && (
        <p role="status" className="success">
          {receipt}
        </p>
      )}
      <label htmlFor="message">Message</label>
      <textarea
        id="message"
        placeholder="Ask Claw to work on something…"
        rows={4}
        value={draft.text}
        disabled={busy || !!pending}
        onChange={(event) => {
          const text = event.target.value;
          update((current) => ({ ...current, text }));
        }}
        onKeyDown={(event) => {
          if (
            (event.metaKey || event.ctrlKey) &&
            event.key === "Enter" &&
            draft.text.trim() &&
            !busy
          ) {
            event.preventDefault();
            void submit();
          }
        }}
      />
      <div className="composer-tools">
        <label className="check-label">
          <input
            type="checkbox"
            checked={draft.separate}
            disabled={busy || !!pending}
            onChange={(event) => {
              const separate = event.target.checked;
              update((current) => ({ ...current, separate }));
            }}
          />
          Queue a separate Run
        </label>
        <small>Otherwise, always steer · Ctrl/⌘ + Enter</small>
      </div>
      <details>
        <summary>Attach retained files or upload</summary>
        <div className="attachment-choices">
          {files.map((file) => (
            <label className="check-label" key={file.id}>
              <input
                type="checkbox"
                disabled={busy || !!pending}
                checked={draft.files.includes(file.id)}
                onChange={(event) => {
                  const checked = event.target.checked;
                  update((current) => ({
                    ...current,
                    files: checked
                      ? [...current.files, file.id]
                      : current.files.filter((id) => id !== file.id),
                  }));
                }}
              />
              {file.name}
            </label>
          ))}
          <label>
            Upload file (8 MiB maximum)
            <input
              type="file"
              disabled={busy || !!pending || !!upload.payload}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) void uploadFile(file);
              }}
            />
          </label>
          {upload.payload && (
            <button
              disabled={busy}
              onClick={() => void uploadFile(upload.payload!.file)}
            >
              {upload.busy
                ? "Uploading…"
                : `Retry same upload: ${upload.payload.file.name}`}
            </button>
          )}
        </div>
      </details>
      <div className="composer-footer">
        <small>Drafts and access tokens stay in this tab's memory.</small>
        <button
          className="primary"
          disabled={busy || !!upload.payload || !draft.text.trim()}
          onClick={() => void submit()}
        >
          {busy
            ? "Submitting…"
            : pending
              ? "Reconcile / retry same input"
              : "Send message"}
        </button>
      </div>
    </section>
  );
}

function ThreadSettings({
  api,
  snapshot,
  profiles,
  done,
}: {
  api: Api;
  snapshot: Thread;
  profiles: Resource[];
  done: () => void;
}) {
  const [title, setTitle] = useState(snapshot.title);
  const [profile, setProfile] = useState(snapshot.profile_id);
  const [archived, setArchived] = useState(snapshot.archived);
  return (
    <section className="panel form-panel">
      <h2>Next-Run settings</h2>
      <p>
        Active and queued Runs keep their captured configuration. Shared
        workspace files are not changed by archiving.
      </p>
      <label>
        Title
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <label>
        Profile
        <select
          value={profile}
          onChange={(event) => setProfile(event.target.value)}
        >
          {profiles.map((item) => (
            <option key={item.id}>{item.id}</option>
          ))}
        </select>
      </label>
      <label className="check-label">
        <input
          type="checkbox"
          checked={archived}
          onChange={(event) => setArchived(event.target.checked)}
        />
        Archive this Thread
      </label>
      <div className="toolbar">
        <Action
          disabled={!title.trim()}
          run={async () => {
            await api.send(
              `/threads/${snapshot.id}`,
              {
                title,
                profile_id: profile,
                expected_version: snapshot.version,
                archived,
              },
              "PUT",
            );
            done();
          }}
        >
          Save settings
        </Action>
        <button onClick={done}>Close without saving</button>
      </div>
    </section>
  );
}
function SavedHistory({
  api,
  thread,
  active,
}: {
  api: Api;
  thread: Thread;
  active: boolean;
}) {
  const history = useRemote<unknown>(
    api,
    `/threads/${thread.id}/history`,
    2000,
    active,
  );
  return (
    <section className="panel">
      <h2>Selected checkpoint history</h2>
      <p className="subtle">
        Saved model messages, including tool calls. Retained content is
        displayed as text, never executed.
      </p>
      <ErrorNotice>{history.error}</ErrorNotice>
      <pre>{pretty(history.data)}</pre>
    </section>
  );
}
function DecisionPanel({
  active,
  api,
  run,
  actor,
  done,
}: {
  api: Api;
  run: Run;
  actor: Principal;
  active: boolean;
  done: () => void;
}) {
  const decisions = useRemote<Decision[]>(
    api,
    `/runs/${run.id}/decisions`,
    1500,
    active,
  );
  return (
    <section className="decision">
      <h2>Waiting for a decision</h2>
      <p>
        Ordinary messages stay held until this exact decision batch is answered.
        Review each request.
      </p>
      <ErrorNotice>{decisions.error}</ErrorNotice>
      {decisions.data
        ?.filter((item) => item.response === null)
        .map((item) => (
          <DecisionForm
            key={item.id}
            api={api}
            decision={item}
            enabled={allowed(actor, "decide")}
            done={done}
          />
        ))}
    </section>
  );
}
function DecisionForm({
  api,
  decision,
  enabled,
  done,
}: {
  api: Api;
  decision: Decision;
  enabled: boolean;
  done: () => void;
}) {
  const [approvals, setApprovals] = useState<Record<string, string>>({});
  const [calls, setCalls] = useState<Record<string, string>>({});
  const ready =
    decision.requests.approvals.every((call) => approvals[call.tool_call_id]) &&
    decision.requests.calls.every((call) => calls[call.tool_call_id]?.trim());
  return (
    <div>
      {decision.requests.approvals.map((call) => (
        <div className="request" key={call.tool_call_id}>
          <h3>{call.tool_name}</h3>
          <pre>
            {typeof call.args === "string" ? call.args : pretty(call.args)}
          </pre>
          <label>
            Permission
            <select
              disabled={!enabled}
              value={approvals[call.tool_call_id] ?? ""}
              onChange={(event) =>
                setApprovals({
                  ...approvals,
                  [call.tool_call_id]: event.target.value,
                })
              }
            >
              <option value="">Choose explicitly…</option>
              <option value="allow">Approve this call</option>
              <option value="deny">Deny this call</option>
            </select>
          </label>
        </div>
      ))}
      {decision.requests.calls.map((call) => (
        <div className="request" key={call.tool_call_id}>
          <h3>{call.tool_name}</h3>
          <pre>
            {typeof call.args === "string" ? call.args : pretty(call.args)}
          </pre>
          <label>
            Response (JSON value)
            <textarea
              disabled={!enabled}
              placeholder={'"Your answer"'}
              value={calls[call.tool_call_id] ?? ""}
              onChange={(event) =>
                setCalls({ ...calls, [call.tool_call_id]: event.target.value })
              }
            />
          </label>
        </div>
      ))}
      <Action
        disabled={!ready || !enabled}
        run={async () => {
          await api.send(`/decisions/${decision.id}/answer`, {
            approvals: Object.fromEntries(
              Object.entries(approvals).map(([id, value]) => [
                id,
                value === "allow",
              ]),
            ),
            calls: Object.fromEntries(
              Object.entries(calls).map(([id, value]) => [
                id,
                JSON.parse(value),
              ]),
            ),
          });
          done();
        }}
      >
        Submit complete decision batch
      </Action>
    </div>
  );
}
function Recovery({
  api,
  run,
  done,
}: {
  api: Api;
  run: Run;
  done: () => void;
}) {
  const [note, setNote] = useState("");
  const [text, setText] = useState("");
  const [id] = useState(requestId);
  return (
    <section className="recovery">
      <h3>Review before recovery</h3>
      <p>
        Cancellation does not roll back external effects. Recovery creates a new
        linked Run; it does not replay unknown tool effects.
      </p>
      <label>
        External-effects review
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Describe what you checked and which effects are known."
        />
      </label>
      <Action
        disabled={!note.trim()}
        run={async () => {
          await api.send(`/runs/${run.id}/reconcile`, { note });
          done();
        }}
      >
        Record review
      </Action>
      {run.reconciled_at && (
        <>
          <p className="success">
            Review recorded {new Date(run.reconciled_at).toLocaleString()}
          </p>
          <label>
            Recovery instruction
            <textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
          </label>
          <Action
            disabled={!text.trim()}
            run={async () => {
              await api.send(`/runs/${run.id}/recover`, {
                request_id: id,
                text,
              });
              done();
            }}
          >
            Start linked recovery
          </Action>
        </>
      )}
    </section>
  );
}
function ReviewInput({
  api,
  input,
  done,
}: {
  api: Api;
  input: Input;
  done: () => void;
}) {
  const [note, setNote] = useState("");
  return (
    <div className="recovery">
      <p>
        {input.disposition === "blocked"
          ? "Discard only work you have reviewed as unapplied. This retains its audit record and does not restore revoked authority."
          : "Delivery was observed, incorporation was not proved. Review it; do not automatically replay."}
      </p>
      <label>
        Review note
        <input value={note} onChange={(event) => setNote(event.target.value)} />
      </label>
      <Action
        disabled={!note.trim()}
        run={async () => {
          await api.send(
            `/inputs/${input.id}/${input.disposition === "blocked" ? "discard" : "acknowledge"}`,
            { note },
          );
          done();
        }}
      >
        {input.disposition === "blocked"
          ? "Discard blocked input"
          : "Acknowledge as unapplied"}
      </Action>
    </div>
  );
}
function Fork({
  api,
  run,
  profiles,
  done,
}: {
  api: Api;
  run: Run;
  profiles: Resource[];
  done: (id: string) => void;
}) {
  const [title, setTitle] = useState("Forked conversation");
  const [profile, setProfile] = useState(profiles[0]?.id ?? "");
  const command = useCommand<
    {
      checkpoint_id: string | null;
      request_id: string;
      title: string;
      profile_id: string;
    },
    Thread
  >(`fork:${run.id}`);
  return (
    <details>
      <summary>Fork saved checkpoint</summary>
      <p>
        Creates independent history with the same shared workspace. Pending
        work, targets and retained files are not copied.
      </p>
      <label>
        New Thread title
        <input
          value={command.payload?.title ?? title}
          disabled={!!command.payload}
          onChange={(event) => setTitle(event.target.value)}
        />
      </label>
      <label>
        Profile
        <select
          value={command.payload?.profile_id ?? profile}
          disabled={!!command.payload}
          onChange={(event) => setProfile(event.target.value)}
        >
          {profiles.map((item) => (
            <option key={item.id}>{item.id}</option>
          ))}
        </select>
      </label>
      <ErrorNotice>{command.error}</ErrorNotice>
      <Action
        disabled={
          command.busy || (!command.payload && (!title.trim() || !profile))
        }
        run={async () => {
          const next = await command.execute(
            () => ({
              checkpoint_id: run.checkpoint_id,
              request_id: requestId(),
              title,
              profile_id: profile,
            }),
            (value) => api.send<Thread>("/threads/fork", value),
          );
          if (next) done(next.id);
        }}
      >
        {command.result
          ? "Open created fork"
          : command.payload
            ? "Retry same fork"
            : "Create fork"}
      </Action>
      {command.result && (
        <button onClick={command.clear}>Start another fork</button>
      )}
    </details>
  );
}
function ChildRuns({
  active,
  api,
  run,
  navigate,
}: {
  api: Api;
  run: Run;
  navigate: (id: string) => void;
  active: boolean;
}) {
  const children = useRemote<Child[]>(
    api,
    `/runs/${run.id}/children`,
    2000,
    active,
    (items) =>
      activeStatuses.has(run.status) ||
      items.some(
        (child) =>
          activeStatuses.has(child.status) ||
          (child.notify && !child.delivery_input_id && !child.delivery_error),
      ),
  );
  return (
    <section>
      <ErrorNotice>{children.error}</ErrorNotice>
      {children.data?.map((child) => (
        <div className="child-run" key={child.id}>
          <div className="toolbar">
            <button onClick={() => navigate(child.child_thread_id)}>
              Open child Thread
            </button>
            <Status value={child.status} />
          </div>
          <small>
            Parent cancellation: {child.cancel_policy} · notification:{" "}
            {child.notify
              ? child.delivery_input_id
                ? "delivered"
                : (child.delivery_error ?? "pending")
              : "off"}
          </small>
          {child.output && <div className="prose">{child.output}</div>}
          {child.error && <ErrorNotice>{child.error}</ErrorNotice>}
        </div>
      ))}
    </section>
  );
}
