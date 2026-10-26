import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { data, type Schema } from "../api/client";
import { useApi } from "../api/session";
import {
  Badge,
  Info,
  Empty,
  ErrorNotice,
  Loading,
  NumberValue,
  Panel,
  Raw,
} from "../components/ui";
import { IconButton } from "../components/icons";
import { exact, money, timestamp } from "../format";

type ThreadProps = { threadId: string };
export function ThreadHistory({
  threadId,
  continuation,
}: ThreadProps & { continuation?: string | null }) {
  const api = useApi();
  const [cursor, setCursor] = useState<string>();
  const history = useQuery({
    queryKey: ["thread", threadId, "history", continuation, cursor],
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/threads/{thread_id}/history", {
          signal,
          params: {
            path: { thread_id: threadId },
            query: {
              cursor,
              expected_continuation_id: continuation ?? undefined,
              limit: 30,
            },
          },
        }),
      ),
  });
  const view = history.data;
  return (
    <Panel
      title="Saved history"
      actions={
        <span className="hint">
          {view ? `${view.entries.length} of ${view.total} entries` : ""}
        </span>
      }
    >
      <ErrorNotice
        error={history.error}
        retry={() => {
          setCursor(undefined);
          void history.refetch();
        }}
      />
      {history.isPending && <Loading />}
      {view && (
        <>
          <div className="pagination">
            <IconButton
              icon="left"
              label="Older"
              disabled={!view.next_cursor}
              onClick={() => setCursor(view.next_cursor!)}
            />
            <IconButton
              icon="right"
              label="Newer"
              disabled={!view.newer_cursor}
              onClick={() => setCursor(view.newer_cursor!)}
            />
            <IconButton
              icon="refresh"
              label="Latest"
              disabled={!cursor}
              onClick={() => setCursor(undefined)}
            />
          </div>
          {!view.entries.length && <Empty title="No saved history" />}
          <div className="transcript">
            {view.entries.map((entry) => (
              <article className="transcript-entry" key={entry.position}>
                <header>
                  <span className="eyebrow">
                    {entry.message_kind} · {entry.position + 1}
                  </span>
                  <time className="hint">{timestamp(entry.timestamp)}</time>
                </header>
                {entry.parts.map((part, index) => (
                  <TranscriptPart key={index} part={part} />
                ))}
                {entry.failures.map((failure) => (
                  <div className="notice error" key={failure.id}>
                    {failure.message}
                    <span className="subline">
                      {failure.code} · {failure.run_id}
                    </span>
                  </div>
                ))}
              </article>
            ))}
          </div>
        </>
      )}
    </Panel>
  );
}
export function TranscriptPart({ part }: { part: Schema["TranscriptPart"] }) {
  const content = (
    <>
      {part.text && <pre className="message-text">{part.text}</pre>}
      {part.value != null && (
        <pre>
          {typeof part.value === "string"
            ? part.value
            : JSON.stringify(part.value, null, 2)}
        </pre>
      )}
      {(part.text_truncated || part.value_omitted) && (
        <p className="hint">Saved excerpt</p>
      )}
      {part.content_parts.length > 0 && (
        <Raw title="Media and structured content" value={part.content_parts} />
      )}
      {part.tool_images.map((image) => (
        <p className="hint" key={image.attachment.attachment_id}>
          Retained image: {image.attachment.name} (
          {exact(image.attachment.size)} bytes)
        </p>
      ))}
      {part.tool_image_unavailable && (
        <p className="hint">Tool image unavailable.</p>
      )}
    </>
  );
  if (part.kind === "user" || part.kind === "assistant")
    return (
      <section className="message-part">
        <h3>{part.kind === "user" ? "User" : "Assistant"}</h3>
        {content}
      </section>
    );
  return (
    <details className="message-part">
      <summary>
        {part.tool_name || part.kind.replaceAll("_", " ")}
        {part.outcome && (
          <>
            {" "}
            · <Badge>{part.outcome}</Badge>
          </>
        )}
      </summary>
      {content}
    </details>
  );
}

export function ThreadTools({ threadId }: ThreadProps) {
  const api = useApi();
  const [offset, setOffset] = useState(0);
  const tools = useQuery({
    queryKey: ["thread", threadId, "tools", offset],
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/threads/{thread_id}/tool-executions", {
          signal,
          params: {
            path: { thread_id: threadId },
            query: { offset, limit: 20 },
          },
        }),
      ),
  });
  return (
    <Panel
      title="Tool executions"
      actions={
        <Info label="Tool status details">
          Tool execution status is tracked separately from Run status.
        </Info>
      }
    >
      <ErrorNotice error={tools.error} retry={() => void tools.refetch()} />
      {tools.isPending && <Loading />}
      {tools.data && (
        <>
          {!tools.data.length ? (
            <Empty title="No tool executions" />
          ) : (
            <div className="table-scroll">
              <table className="responsive-table">
                <thead>
                  <tr>
                    <th>Tool</th>
                    <th>Status</th>
                    <th>Run</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {tools.data.map((tool) => (
                    <tr key={tool.execution_id}>
                      <td className="row-title" data-label="Tool">
                        {tool.tool_name}
                        <span className="subline">
                          <code>{tool.execution_id}</code>
                        </span>
                      </td>
                      <td data-label="Status">
                        <Badge>{tool.status}</Badge>
                        <span className="subline">
                          {tool.result_available
                            ? "Result retained"
                            : "Result unavailable"}
                          {tool.foreground_released
                            ? " · Foreground released"
                            : ""}
                        </span>
                      </td>
                      <td data-label="Run">
                        <code>{tool.run_id}</code>
                      </td>
                      <td data-label="Created">{timestamp(tool.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="pagination">
            <IconButton
              icon="left"
              label="Previous"
              disabled={!offset}
              onClick={() => setOffset(offset - 20)}
            />
            <span>Page {offset / 20 + 1}</span>
            <IconButton
              icon="right"
              label="Next"
              disabled={tools.data.length < 20}
              onClick={() => setOffset(offset + 20)}
            />
          </div>
        </>
      )}
    </Panel>
  );
}
function tokenTotal(totals: Schema["UsageTotals"]) {
  const counters = Object.fromEntries(totals.tokens);
  return (counters.input_tokens ?? 0) + (counters.output_tokens ?? 0);
}
function Cost({ totals }: { totals: Schema["UsageTotals"] }) {
  return (
    <>
      {totals.unknown_model_costs === totals.model_requests &&
      totals.model_requests > 0
        ? "Unavailable"
        : money(totals.model_cost_usd)}
      {totals.unknown_model_costs > 0 && (
        <span className="subline">
          {totals.unknown_model_costs} unpriced responses
        </span>
      )}
    </>
  );
}
export function ThreadUsage({ threadId }: ThreadProps) {
  const api = useApi();
  const usage = useQuery({
    queryKey: ["thread", threadId, "usage"],
    queryFn: async ({ signal }) => {
      const view = await data(
        api.GET("/api/threads/{thread_id}/usage", {
          signal,
          params: { path: { thread_id: threadId } },
        }),
      );
      // openapi-fetch's Readable maps native tuple pairs to arrays. The server's
      // response model and generated schema preserve these exact ledger tuples.
      return view as Schema["ThreadUsageView"];
    },
  });
  const view = usage.data;
  return (
    <>
      <ErrorNotice error={usage.error} retry={() => void usage.refetch()} />
      {usage.isPending && <Loading />}
      {view && (
        <>
          <p className="hint">Updated {timestamp(view.observed_through)}</p>
          <div className="cards">
            {(
              [
                ["Combined", view.combined],
                ["Root", view.root],
                ["Descendants", view.descendants],
              ] as const
            ).map(([label, totals]) => (
              <Panel key={label}>
                <div className="metric">
                  <span className="metric-label">{label} Tokens</span>
                  <span className="metric-value">
                    <NumberValue value={tokenTotal(totals)} />
                  </span>
                  <Cost totals={totals} />
                  <span className="subline">
                    {exact(totals.model_requests)} model responses
                  </span>
                </div>
              </Panel>
            ))}
          </div>
          <Panel
            title="Token counters"
            actions={
              <Info label="Token counter details">
                Input and output include cache and audio tokens.
              </Info>
            }
          >
            <div className="panel-body">
              <dl className="facts">
                {view.combined.tokens.map(([key, value]) => (
                  <div className="fact-row" key={key}>
                    <dt>{key.replaceAll("_", " ")}</dt>
                    <dd>{exact(value)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </Panel>
          <Panel title="Recent Runs">
            <div className="panel-body">
              <p className="hint">
                Other Runs: {exact(tokenTotal(view.other_runs))} tokens
              </p>
            </div>
            <div className="table-scroll">
              <table className="responsive-table">
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Scope</th>
                    <th className="numeric">Tokens</th>
                    <th className="numeric">Estimated cost</th>
                  </tr>
                </thead>
                <tbody>
                  {view.recent_runs.map((run) => (
                    <tr key={`${run.run_id}:${run.agent_instance_id}`}>
                      <td className="row-title" data-label="Run">
                        <code>{run.run_id}</code>
                        <span className="subline">{run.agent_instance_id}</span>
                      </td>
                      <td data-label="Scope">
                        {run.descendant ? "Descendant" : "Root"}
                      </td>
                      <td className="numeric" data-label="Tokens">
                        {exact(tokenTotal(run.totals))}
                      </td>
                      <td className="numeric" data-label="Cost">
                        <Cost totals={run.totals} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!view.recent_runs.length && (
              <Empty title="No recorded model responses" />
            )}
          </Panel>
          <Raw
            title="Model, group, and provider cost breakdown"
            value={{
              models: view.models,
              model_scopes: view.model_scopes,
              groups: view.groups,
              other_models: view.other_models,
              other_groups: view.other_groups,
              provider_costs: view.combined.provider_costs,
            }}
          />
        </>
      )}
    </>
  );
}

export function ThreadConfiguration({ threadId }: ThreadProps) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["thread", threadId, "configuration"],
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/threads/{thread_id}/configuration", {
          signal,
          params: { path: { thread_id: threadId } },
        }),
      ),
  });
  const view = query.data;
  return (
    <>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading />}
      {view && (
        <div className="split">
          <Panel title="Next Run">
            <div className="panel-body stack">
              <dl className="facts">
                <dt>Model</dt>
                <dd>{view.next_model_id ?? "Unavailable"}</dd>
                <dt>Project</dt>
                <dd>{view.next_run.configuration.project_id ?? "None"}</dd>
                <dt>Environment</dt>
                <dd>{view.next_run.configuration.environment_profile_id}</dd>
                <dt>Local roots</dt>
                <dd>
                  {view.next_run.configuration.local_roots.join(", ") || "None"}
                </dd>
                <dt>Capabilities</dt>
                <dd>{view.next_capability_ids.join(", ") || "None"}</dd>
              </dl>
              <Raw
                value={view.next_run}
                title="Next selections and provenance"
              />
            </div>
          </Panel>
          <Panel title="Captured execution">
            <div className="panel-body stack">
              <p className="hint">
                Source: {view.capture_source.replaceAll("_", " ")}
              </p>
              {view.captured ? (
                <>
                  <dl className="facts">
                    <dt>Model</dt>
                    <dd>{view.captured.agent.model_id}</dd>
                    <dt>Agent</dt>
                    <dd>{view.captured.agent.name}</dd>
                    <dt>Project</dt>
                    <dd>{view.captured.project_id ?? "None"}</dd>
                    <dt>Project roots</dt>
                    <dd>{view.captured.project_roots.join(", ") || "None"}</dd>
                    <dt>Memory</dt>
                    <dd>{view.captured.memory_scopes.join(", ") || "None"}</dd>
                    <dt>Environment</dt>
                    <dd>{view.captured.environment_profile_id ?? "None"}</dd>
                  </dl>
                  <Raw value={view.captured} title="Captured configuration" />
                </>
              ) : (
                <p>No captured execution configuration.</p>
              )}
            </div>
          </Panel>
        </div>
      )}
    </>
  );
}

export function ThreadBusiness({ threadId }: ThreadProps) {
  const api = useApi();
  const query = useQuery({
    queryKey: ["thread", threadId, "business"],
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/threads/{thread_id}/business", {
          signal,
          params: { path: { thread_id: threadId } },
        }),
      ),
  });
  const view = query.data;
  return (
    <>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending && <Loading />}
      {view && (
        <>
          <p className="hint">Latest {view.limit} records per source</p>
          <div className="cards">
            <Panel title="Inbox">
              {view.inbox.length ? (
                view.inbox.map((item) => (
                  <div className="record" key={item.id}>
                    <Badge>{item.disposition}</Badge>
                    <p>
                      <code>{item.id}</code>
                    </p>
                    <p>
                      {item.connection} · {item.channel_id ?? "No channel"}
                    </p>
                    <p className="hint">
                      {item.reason}
                      {item.blocked && ` · Blocked: ${item.blocked}`}
                    </p>
                  </div>
                ))
              ) : (
                <Empty title="No linked Inbox records" />
              )}
            </Panel>
            <Panel title="Dispatch attempts">
              {view.attempts.length ? (
                view.attempts.map((item) => (
                  <div className="record" key={item.id}>
                    <Badge>{item.status}</Badge>
                    <p>
                      {item.kind} · <code>{item.id}</code>
                    </p>
                    <p className="hint">
                      Receipt: {item.receipt_id ?? "Unavailable"}
                    </p>
                    <p className="hint">{item.evidence}</p>
                  </div>
                ))
              ) : (
                <Empty title="No linked dispatch attempts" />
              )}
            </Panel>
            <Panel title="Scheduled occurrences">
              {view.occurrences.length ? (
                view.occurrences.map((item) => (
                  <div className="record" key={item.id}>
                    <Badge>{item.status}</Badge>
                    <p>
                      <Link
                        to={`/schedules?occurrence=${encodeURIComponent(item.id)}`}
                      >
                        {item.schedule_id}
                      </Link>
                    </p>
                    <p>
                      <code>{item.id}</code>
                    </p>
                    <p className="hint">{item.detail}</p>
                  </div>
                ))
              ) : (
                <Empty title="No linked occurrences" />
              )}
            </Panel>
            <Panel title="Result delivery">
              {view.deliveries.length ? (
                view.deliveries.map((item) => (
                  <div className="record" key={item.id}>
                    <Badge>{item.status}</Badge>
                    <p>{item.channel_id}</p>
                    <p>
                      <code>{item.id}</code>
                    </p>
                    <p className="hint">{item.evidence}</p>
                  </div>
                ))
              ) : (
                <Empty title="No linked deliveries" />
              )}
            </Panel>
          </div>
        </>
      )}
    </>
  );
}
