import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { data, type Schema } from "../api/client";
import { useApi } from "../api/session";
import {
  Badge,
  ConfirmAction,
  Copy,
  Empty,
  ErrorNotice,
  Loading,
  Modal,
  PageHeader,
  Panel,
  Raw,
} from "../components/ui";
import { timestamp } from "../format";
import { ScheduleEditor } from "./schedule-editor";

type Definition = Schema["ScheduleDefinition"];
type Edit = { id?: string; definition?: Definition; digest: string | null };
function timingLabel(timing: Schema["Timing"]) {
  return timing.kind === "cron"
    ? timing.cron
    : timing.kind === "interval"
      ? `Every ${timing.seconds}s`
      : timestamp(timing.at);
}
export function Schedules() {
  const api = useApi();
  const cache = useQueryClient();
  const [params, setParams] = useSearchParams();
  const scheduleId = params.get("schedule") || undefined;
  const occurrenceId = params.get("occurrence");
  const [edit, setEdit] = useState<Edit | null>(null);
  const [trigger, setTrigger] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{
    title: string;
    description: string;
    action: () => Promise<unknown>;
  } | null>(null);
  const query = useQuery({
    queryKey: ["schedules", "status"],
    queryFn: ({ signal }) => data(api.GET("/api/schedules/status", { signal })),
    refetchInterval: 15000,
  });
  const history = useQuery({
    queryKey: ["schedules", "occurrences", scheduleId],
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/occurrences", {
          signal,
          params: { query: { schedule_id: scheduleId, limit: 50 } },
        }),
      ),
    refetchInterval: 15000,
  });
  const reload = useMutation({
    mutationFn: () => data(api.POST("/api/schedules/reload")),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["schedules"] });
    },
  });
  const view = query.data;
  const update = (key: string, value: string | null) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      value ? next.set(key, value) : next.delete(key);
      return next;
    });
  const write = async (
    id: string,
    definition: Definition | null,
    digest: string | null,
  ) => {
    await data(
      api.PUT("/api/schedules/{schedule_id}", {
        params: { path: { schedule_id: id } },
        body: { definition, expected_digest: digest },
      }),
    );
    void cache.invalidateQueries({ queryKey: ["schedules"] });
  };
  return (
    <>
      <PageHeader
        title="Schedules"
        description="Scheduled work, retained next-due times, and independent execution and delivery outcomes."
        actions={
          <>
            <button disabled={reload.isPending} onClick={() => reload.mutate()}>
              {reload.isPending ? "Reloading…" : "Reload catalog"}
            </button>
            <button
              className="primary"
              disabled={!view}
              onClick={() => setEdit({ digest: view!.digest })}
            >
              Create schedule
            </button>
          </>
        }
      />
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      <ErrorNotice error={reload.error} />
      {view?.error && (
        <div className="notice error" role="alert">
          {view.error}
        </div>
      )}
      {query.isPending && <Loading />}
      {view && (
        <Panel
          title="Accepted schedules"
          actions={<span className="hint">Updates every 15s</span>}
        >
          {Object.entries(view.catalog.schedules ?? {}).length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Schedule</th>
                    <th>Timing</th>
                    <th>Next due</th>
                    <th>Latest occurrence</th>
                    <th>
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(view.catalog.schedules ?? {}).map(
                    ([id, definition]) => {
                      const status = view.status[id];
                      return (
                        <tr key={id}>
                          <td>
                            <button onClick={() => update("schedule", id)}>
                              {definition.name}
                            </button>
                            <span className="subline">
                              <code>{id}</code>
                            </span>
                            <span className="subline">
                              {definition.project_id}
                            </span>
                          </td>
                          <td>
                            {timingLabel(definition.timing)}
                            <span className="subline">
                              {definition.timing.timezone}
                            </span>
                          </td>
                          <td>
                            <Badge>
                              {status?.state ?? "pending_discovery"}
                            </Badge>
                            <span className="subline">
                              {status?.next_due
                                ? timestamp(status.next_due)
                                : "—"}
                            </span>
                          </td>
                          <td>
                            {status?.latest ? (
                              <>
                                <Badge>{status.latest.status}</Badge>
                                <span className="subline">
                                  <button
                                    onClick={() =>
                                      update("occurrence", status.latest!.id)
                                    }
                                  >
                                    Inspect
                                  </button>
                                </span>
                              </>
                            ) : (
                              <span className="muted">No occurrence</span>
                            )}
                          </td>
                          <td>
                            <div className="actions">
                              <button
                                onClick={() =>
                                  setEdit({
                                    id,
                                    definition,
                                    digest: view.digest,
                                  })
                                }
                              >
                                Edit
                              </button>
                              <button
                                disabled={!definition.enabled}
                                onClick={() => setTrigger(id)}
                              >
                                Run now
                              </button>
                              <button
                                onClick={() =>
                                  setConfirm({
                                    title: `${definition.enabled ? "Pause" : "Enable"} ${definition.name}?`,
                                    description: definition.enabled
                                      ? "Future dispatch is paused. Already admitted work and retained history remain."
                                      : "The scheduler will discover this definition and resume eligible work.",
                                    action: () =>
                                      write(
                                        id,
                                        {
                                          ...definition,
                                          enabled: !definition.enabled,
                                        },
                                        view.digest,
                                      ),
                                  })
                                }
                              >
                                {definition.enabled ? "Pause" : "Enable"}
                              </button>
                              <button
                                className="danger"
                                onClick={() =>
                                  setConfirm({
                                    title: `Delete ${definition.name}?`,
                                    description:
                                      "Remove this definition. Retained occurrences, Threads and delivery records remain; admitted work is not cancelled.",
                                    action: () => write(id, null, view.digest),
                                  })
                                }
                              >
                                Delete
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    },
                  )}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="No schedules yet">
              Create a schedule to run isolated work with an explicit Project
              and timing.
            </Empty>
          )}
        </Panel>
      )}
      <Panel
        title="Occurrence history"
        actions={
          <button
            disabled={history.isFetching}
            onClick={() => void history.refetch()}
          >
            Refresh
          </button>
        }
      >
        <div className="panel-body toolbar">
          <label>
            Schedule
            <input
              aria-label="Filter occurrence history by schedule ID"
              value={scheduleId ?? ""}
              onChange={(event) =>
                update("schedule", event.target.value || null)
              }
              placeholder="All schedules"
            />
          </label>
          {scheduleId && (
            <button onClick={() => update("schedule", null)}>Clear</button>
          )}
          <span className="hint">Latest 50 retained occurrences</span>
        </div>
        <ErrorNotice
          error={history.error}
          retry={() => void history.refetch()}
        />
        {history.isPending && <Loading />}
        {history.data &&
          (history.data.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Occurrence</th>
                    <th>Execution status</th>
                    <th>Due (local)</th>
                    <th>Thread</th>
                  </tr>
                </thead>
                <tbody>
                  {history.data.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <button onClick={() => update("occurrence", item.id)}>
                          {item.schedule_id}
                        </button>
                        <span className="subline">
                          <code>{item.id}</code>
                        </span>
                      </td>
                      <td>
                        <Badge>{item.status}</Badge>
                        <span className="subline">{item.detail}</span>
                      </td>
                      <td>{timestamp(item.due)}</td>
                      <td>
                        {item.thread_id ? (
                          <Link to={`/runs/${item.thread_id}`}>
                            {item.thread_id}
                          </Link>
                        ) : (
                          "Not assigned"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="No matching occurrences" />
          ))}
      </Panel>
      {edit && <ScheduleEditor {...edit} close={() => setEdit(null)} />}
      {trigger && (
        <ManualTrigger
          scheduleId={trigger}
          close={() => setTrigger(null)}
          inspect={(id) => {
            setTrigger(null);
            update("occurrence", id);
          }}
        />
      )}
      {confirm && <ConfirmAction {...confirm} close={() => setConfirm(null)} />}
      {occurrenceId && (
        <OccurrenceDetail
          key={occurrenceId}
          id={occurrenceId}
          close={() => update("occurrence", null)}
        />
      )}
    </>
  );
}

type TriggerAttempt = { requestKey: string; result?: Schema["Occurrence"] };
export function ManualTrigger({
  scheduleId,
  close,
  inspect,
}: {
  scheduleId: string;
  close: () => void;
  inspect: (id: string) => void;
}) {
  const api = useApi();
  const cache = useQueryClient();
  const key = ["schedule-trigger", scheduleId];
  const [attempt, setAttempt] = useState(() =>
    cache.getQueryData<TriggerAttempt>(key),
  );
  const mutation = useMutation({
    mutationFn: (requestKey: string) =>
      data(
        api.POST("/api/schedules/{schedule_id}/trigger", {
          params: { path: { schedule_id: scheduleId } },
          body: { request_key: requestKey },
        }),
      ),
    onSuccess: (result, requestKey) => {
      const completed = { requestKey, result };
      cache.setQueryData(key, completed);
      setAttempt(completed);
      void cache.invalidateQueries({ queryKey: ["schedules"] });
    },
  });
  const submit = () => {
    const next = attempt ?? { requestKey: crypto.randomUUID() };
    cache.setQueryDefaults(key, { gcTime: Infinity });
    cache.setQueryData(key, next);
    setAttempt(next);
    mutation.mutate(next.requestKey);
  };
  return (
    <Modal
      title={`Run now · ${scheduleId}`}
      description="Create one manual occurrence using the accepted schedule, Project and delivery destination. This can execute tools and send a result notification."
      close={() => {
        if (!mutation.isPending) close();
      }}
    >
      <div className="stack">
        {attempt && (
          <>
            <Badge>
              {attempt.result
                ? "accepted"
                : mutation.isPending
                  ? "pending"
                  : "unknown"}
            </Badge>
            <Copy value={attempt.requestKey} />
          </>
        )}
        <ErrorNotice error={mutation.error} />
        {attempt?.result ? (
          <>
            <p>
              Occurrence retained: <code>{attempt.result.id}</code>. Acceptance
              is separate from execution and delivery.
            </p>
            <button
              className="primary"
              onClick={() => inspect(attempt.result!.id)}
            >
              Inspect occurrence
            </button>
            <button
              onClick={() => {
                cache.removeQueries({ queryKey: key });
                setAttempt(undefined);
                mutation.reset();
              }}
            >
              Prepare another occurrence
            </button>
          </>
        ) : (
          <>
            {attempt && (
              <p className="hint">
                The outcome is not yet confirmed. Retry uses this same request
                key. Keep it before reloading or disconnecting; closing this
                dialog preserves it for this Console session.
              </p>
            )}
            <div className="form-actions">
              <button disabled={mutation.isPending} onClick={close}>
                Close
              </button>
              <button
                className="primary"
                disabled={mutation.isPending}
                onClick={submit}
              >
                {mutation.isPending
                  ? "Submitting…"
                  : attempt
                    ? "Retry the same request"
                    : "Confirm run now"}
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
function OccurrenceDetail({ id, close }: { id: string; close: () => void }) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["schedules", "occurrence", id],
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/occurrences/{occurrence_id}", {
          signal,
          params: { path: { occurrence_id: id } },
        }),
      ),
    refetchInterval: 15000,
  });
  const deliveries = useQuery({
    queryKey: ["schedules", "occurrence", id, "deliveries"],
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/occurrences/{occurrence_id}/deliveries", {
          signal,
          params: { path: { occurrence_id: id } },
        }),
      ),
    refetchInterval: 15000,
  });
  const item = query.data;
  return (
    <Modal
      title="Occurrence details"
      description="Execution and result delivery are independent retained records. Updates every 15 seconds."
      close={close}
    >
      <div className="stack">
        <Copy value={id} />
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
        {query.isPending && <Loading />}
        {item && (
          <>
            <h3>Execution</h3>
            <Badge>{item.status}</Badge>
            <p>{item.detail || "No additional execution evidence."}</p>
            <dl className="facts">
              <dt>Schedule</dt>
              <dd>{item.schedule_id}</dd>
              <dt>Due</dt>
              <dd>{timestamp(item.due)}</dd>
              <dt>Created</dt>
              <dd>{timestamp(item.created)}</dd>
              <dt>Thread</dt>
              <dd>
                {item.thread_id ? (
                  <Link to={`/runs/${item.thread_id}`}>{item.thread_id}</Link>
                ) : (
                  "Not assigned"
                )}
              </dd>
              <dt>Receipt</dt>
              <dd>
                <code>{item.receipt_id ?? "Unavailable"}</code>
              </dd>
              <dt>Request key</dt>
              <dd>
                <code>{item.request_key}</code>
              </dd>
            </dl>
            {item.result && (
              <details>
                <summary>Retained result</summary>
                <pre className="message-text">{item.result}</pre>
              </details>
            )}
            <Raw
              title="Captured definition and destination"
              value={{
                definition: item.definition,
                destination: item.destination,
              }}
            />
          </>
        )}
        <h3>Result delivery</h3>
        <ErrorNotice
          error={deliveries.error}
          retry={() => void deliveries.refetch()}
        />
        {deliveries.isPending && <Loading />}
        {deliveries.data &&
          (deliveries.data.length ? (
            deliveries.data.map((delivery) => (
              <div className="notice" key={delivery.id}>
                <Badge>{delivery.status}</Badge>
                <p>{delivery.channel_id}</p>
                <p className="hint">{delivery.evidence}</p>
                <code>{delivery.id}</code>
              </div>
            ))
          ) : (
            <p className="muted">
              No retained result delivery. Notification policy, pending
              execution or an unavailable result may explain its absence.
            </p>
          ))}
      </div>
    </Modal>
  );
}
