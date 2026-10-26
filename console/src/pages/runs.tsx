import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router";
import { data, type Schema } from "../api/client";
import { useApi } from "../api/session";
import { useThreadWatch } from "../api/watch";
import {
  Badge,
  Copy,
  Empty,
  ErrorNotice,
  Loading,
  PageHeader,
  Panel,
  Raw,
} from "../components/ui";
import { timestamp } from "../format";
import {
  ThreadHistory,
  ThreadTools,
  ThreadUsage,
  ThreadConfiguration,
  ThreadBusiness,
} from "./thread-panels";

export function Runs() {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const search = params.get("q") || undefined;
  const project = params.get("project") || undefined;
  const cursor = params.get("cursor") || undefined;
  const archived = params.get("archived") === "1";
  const threads = useQuery({
    queryKey: ["threads", search, project, cursor, archived],
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/threads", {
          signal,
          params: {
            query: {
              query: search,
              project_id: project,
              cursor,
              include_archived: archived,
              limit: 20,
            },
          },
        }),
      ),
  });
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => data(api.GET("/api/projects", { signal })),
  });
  const update = (key: string, value: string) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("cursor");
      value ? next.set(key, value) : next.delete(key);
      return next;
    });
  return (
    <>
      <PageHeader
        title="Runs"
        description="Inspect saved work, current activity, and exact source records. Continue conversations in IM."
        actions={
          <button
            disabled={threads.isFetching}
            onClick={() => void threads.refetch()}
          >
            Refresh
          </button>
        }
      />
      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          update(
            "q",
            String(
              new FormData(event.currentTarget).get("search") ?? "",
            ).trim(),
          );
        }}
      >
        <input
          key={search}
          name="search"
          aria-label="Search Threads"
          placeholder="Search title or Thread ID"
          defaultValue={search}
        />
        <button type="submit">Search</button>
        <label>
          Project
          <select
            value={project ?? ""}
            onChange={(event) => update("project", event.target.value)}
          >
            <option value="">All projects</option>
            {projects.data?.map((item) => (
              <option key={item.project_id} value={item.project_id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={archived}
            onChange={(event) =>
              update("archived", event.target.checked ? "1" : "")
            }
          />
          Include archived
        </label>
      </form>
      <ErrorNotice error={threads.error} retry={() => void threads.refetch()} />
      {threads.isPending ? (
        <Loading />
      ) : (
        threads.data && (
          <Panel
            title="Threads"
            actions={
              <span className="hint">{threads.data.total} matching</span>
            }
          >
            {threads.data.threads.length ? (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Thread</th>
                      <th>Activity / latest attempt</th>
                      <th>Project</th>
                      <th>Updated</th>
                    </tr>
                  </thead>
                  <tbody>
                    {threads.data.threads.map((thread) => (
                      <tr key={thread.thread_id}>
                        <td>
                          <Link to={`/runs/${thread.thread_id}`}>
                            {thread.title || "Untitled Thread"}
                          </Link>
                          <span className="subline">
                            <code>{thread.thread_id}</code>
                          </span>
                          <span className="subline">
                            {thread.role}
                            {thread.archived ? " · Archived" : ""}
                          </span>
                        </td>
                        <td>
                          <Activity thread={thread} />
                        </td>
                        <td>
                          {projects.data?.find(
                            (item) =>
                              item.project_id ===
                              thread.configuration.project_id,
                          )?.name ||
                            thread.configuration.project_id ||
                            "No project"}
                        </td>
                        <td>
                          {timestamp(thread.activity_at ?? thread.updated_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty title="No matching Threads">
                Work created in IM or through the CLI will appear here.
              </Empty>
            )}
            <div className="pagination">
              {cursor && (
                <button onClick={() => update("cursor", "")}>First page</button>
              )}
              <button
                disabled={!threads.data.next_cursor}
                onClick={() => update("cursor", threads.data!.next_cursor!)}
              >
                Next page
              </button>
            </div>
          </Panel>
        )
      )}
    </>
  );
}

function Activity({ thread }: { thread: Schema["ThreadSummary"] }) {
  return (
    <div className="stack compact-stack">
      <Badge>{thread.root_activity.state}</Badge>
      <span className="hint">
        Latest attempt: {thread.last_execution?.status ?? "none"}
      </span>
    </div>
  );
}

export function RunDetail() {
  const { threadId } = useParams();
  return <ThreadDetail key={threadId} threadId={threadId!} />;
}
function ThreadDetail({ threadId }: { threadId: string }) {
  const api = useApi();
  const cache = useQueryClient();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "summary";
  const detail = useQuery({
    queryKey: ["thread", threadId, "detail"],
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/threads/{thread_id}", {
          signal,
          params: { path: { thread_id: threadId } },
        }),
      ),
  });
  const thread = detail.data?.thread;
  const observation = useThreadWatch(
    thread && !thread.parent_thread_id ? threadId : undefined,
  );
  const active = useQuery({
    queryKey: ["thread", threadId, "active"],
    enabled: !!thread && !thread.parent_thread_id,
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/threads/{thread_id}/active", {
          signal,
          params: { path: { thread_id: threadId } },
        }),
      ),
  });
  return (
    <>
      <Link to="/runs" className="back-link">
        ← All Threads
      </Link>
      <PageHeader
        title={thread?.title || "Thread details"}
        description="Read-only investigation. Saved execution, current activity, and delivery have separate outcomes."
        actions={
          <button
            onClick={() =>
              void cache.invalidateQueries({ queryKey: ["thread", threadId] })
            }
          >
            Refresh
          </button>
        }
      />
      <Copy value={threadId} />
      <ErrorNotice error={detail.error} retry={() => void detail.refetch()} />
      {detail.isPending && <Loading />}
      {thread && (
        <>
          <div className="toolbar">
            <Badge>{thread.role}</Badge>
            <Activity thread={thread} />
            <span className="grow" />
            <span className="hint" role="status">
              {observation}
            </span>
          </div>
          <nav className="tabs" aria-label="Thread sections">
            {[
              ["summary", "Summary"],
              ["history", "History"],
              ["usage", "Usage"],
              ["tools", "Tools"],
              ["configuration", "Configuration"],
              ["sources", "Sources"],
            ].map(([value, label]) => (
              <Link
                key={value}
                to={`?tab=${value}`}
                aria-current={tab === value ? "page" : undefined}
              >
                {label}
              </Link>
            ))}
          </nav>
          {tab === "summary" && (
            <>
              <div className="split">
                <Panel title="Latest saved attempt">
                  <div className="panel-body stack">
                    {thread.last_execution ? (
                      <>
                        <Badge>{thread.last_execution.status}</Badge>
                        <dl className="facts">
                          <dt>Execution</dt>
                          <dd>
                            <code>{thread.last_execution.execution_id}</code>
                          </dd>
                          <dt>Run</dt>
                          <dd>
                            <code>
                              {thread.last_execution.run_id ?? "Not captured"}
                            </code>
                          </dd>
                          <dt>Submitted</dt>
                          <dd>
                            {timestamp(thread.last_execution.submitted_at)}
                          </dd>
                          <dt>Started</dt>
                          <dd>{timestamp(thread.last_execution.started_at)}</dd>
                          <dt>Finished</dt>
                          <dd>
                            {timestamp(thread.last_execution.completed_at)}
                          </dd>
                        </dl>
                        {thread.last_execution.error_message && (
                          <div className="notice error">
                            {thread.last_execution.error_message}
                          </div>
                        )}
                        {thread.last_execution.main_account && (
                          <>
                            <h3>Latest MAIN account selection</h3>
                            <p>
                              {thread.last_execution.main_account.account_name}{" "}
                              ·{" "}
                              <code>
                                {thread.last_execution.main_account.account_id}
                              </code>
                            </p>
                            <p className="hint">
                              Execution provenance; historical Token totals are
                              not attributed to this account.
                            </p>
                            <Raw value={thread.last_execution.main_account} />
                          </>
                        )}
                      </>
                    ) : (
                      <p className="muted">No saved attempt.</p>
                    )}
                  </div>
                </Panel>
                <Panel title="Current activity">
                  <div className="panel-body stack">
                    <ErrorNotice
                      error={active.error}
                      retry={() => void active.refetch()}
                    />
                    {active.isLoading ? (
                      <Loading />
                    ) : active.data ? (
                      <>
                        <Badge>{active.data.status}</Badge>
                        <Copy value={active.data.receipt.receipt_id} />
                        <Raw title="Current operation" value={active.data} />
                      </>
                    ) : (
                      <p className="muted">
                        {thread.parent_thread_id
                          ? "Child execution is observed through its root Thread."
                          : "No active root operation observed."}
                      </p>
                    )}
                    <p className="hint">
                      An inactive Thread is not proof of success. Tool
                      executions can outlive a Run.
                    </p>
                    <dl className="facts">
                      <dt>Project</dt>
                      <dd>{thread.configuration.project_id ?? "None"}</dd>
                      <dt>Coordinator</dt>
                      <dd>
                        {thread.coordinator_thread_id ? (
                          <Link to={`/runs/${thread.coordinator_thread_id}`}>
                            {thread.coordinator_thread_id}
                          </Link>
                        ) : (
                          "None"
                        )}
                      </dd>
                      <dt>Parent</dt>
                      <dd>
                        {thread.parent_thread_id ? (
                          <Link to={`/runs/${thread.parent_thread_id}`}>
                            {thread.parent_thread_id}
                          </Link>
                        ) : (
                          "None"
                        )}
                      </dd>
                      <dt>Memory scope</dt>
                      <dd>{thread.memory_scope ?? "None"}</dd>
                    </dl>
                  </div>
                </Panel>
              </div>
              {!!detail.data?.deferred_requests.length && (
                <Panel title="Pending decisions">
                  <div className="panel-body">
                    <p className="hint">
                      Respond through the owning IM conversation or CLI.
                    </p>
                  </div>
                  {detail.data.deferred_requests.map((request) => (
                    <Raw
                      key={request.request_id}
                      value={request}
                      title="Decision details"
                    />
                  ))}
                </Panel>
              )}
              {thread.completion && (
                <Panel title="Latest selected completion">
                  <div className="panel-body">
                    <p>
                      {timestamp(thread.completion.completed_at)} ·{" "}
                      <code>{thread.completion.run_id}</code>
                    </p>
                    <p className="hint">
                      The latest successfully selected saved result may belong
                      to an earlier attempt.
                    </p>
                  </div>
                </Panel>
              )}
            </>
          )}
          {tab === "history" && (
            <ThreadHistory
              key={detail.data?.continuation_id}
              threadId={threadId}
              continuation={detail.data?.continuation_id}
            />
          )}
          {tab === "usage" && <ThreadUsage threadId={threadId} />}
          {tab === "tools" && <ThreadTools threadId={threadId} />}
          {tab === "configuration" && (
            <ThreadConfiguration threadId={threadId} />
          )}
          {tab === "sources" && <ThreadBusiness threadId={threadId} />}
          {![
            "summary",
            "history",
            "usage",
            "tools",
            "configuration",
            "sources",
          ].includes(tab) && (
            <Empty title="Unknown section">
              <button onClick={() => setParams({})}>Return to summary</button>
            </Empty>
          )}
        </>
      )}
    </>
  );
}
