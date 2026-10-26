import { Icon, IconButton } from "../components/icons";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { data, type Schema } from "../api/client";
import { useApi } from "../api/session";
import {
  Empty,
  Info,
  ErrorNotice,
  Loading,
  NumberValue,
  PageHeader,
  Panel,
} from "../components/ui";
import { Allowances } from "../components/allowances";
import { compact, dateRange, exact, money, timestamp } from "../format";
import styles from "./overview.module.css";

export function Overview() {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const defaults = dateRange(7);
  const start = params.get("start") ?? defaults.start;
  const end = params.get("end") ?? defaults.end;
  const group = params.get("group") === "thread" ? "thread" : "model";
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const model = params.get("model") || undefined;
  const project = params.get("project") || undefined;
  const [metric, setMetric] = useState<"tokens" | "cost">("tokens");
  const query = useQuery({
    queryKey: ["usage", start, end, group, offset, model, project],
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/usage", {
          signal,
          params: {
            query: {
              start,
              end,
              group_by: group,
              offset,
              limit: 10,
              model,
              project_id: project,
            },
          },
        }),
      ),
  });
  const update = (values: Record<string, string>) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("offset");
      for (const [key, value] of Object.entries(values))
        value ? next.set(key, value) : next.delete(key);
      return next;
    });
  const view = query.data;
  return (
    <>
      <PageHeader
        title="Overview"
        actions={
          <button
            className="icon-button"
            aria-label="Refresh usage"
            title="Refresh usage"
            onClick={() => void query.refetch()}
            disabled={query.isFetching}
          >
            <Icon
              name="refresh"
              className={query.isFetching ? "spinning" : ""}
            />
          </button>
        }
      />
      <div className="filters">
        <label>
          Period · UTC
          <select
            aria-label="Usage period"
            value={params.get("period") ?? "7"}
            onChange={(e) =>
              update(
                e.target.value === "custom"
                  ? { period: "custom" }
                  : {
                      ...dateRange(Number(e.target.value)),
                      period: e.target.value,
                    },
              )
            }
          >
            <option value="1">Today</option>
            <option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option>
            <option value="custom">Custom</option>
          </select>
        </label>
        {params.get("period") === "custom" && (
          <>
            <label>
              From
              <input
                type="date"
                value={start}
                onChange={(e) => update({ start: e.target.value })}
              />
            </label>
            <label>
              Until (exclusive)
              <input
                type="date"
                value={end}
                onChange={(e) => update({ end: e.target.value })}
              />
            </label>
          </>
        )}
        <label>
          Project
          <select
            value={project ?? ""}
            onChange={(e) => update({ project: e.target.value })}
          >
            <option value="">All projects</option>
            {view?.projects.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Model
          <select
            value={model ?? ""}
            onChange={(e) => update({ model: e.target.value })}
          >
            <option value="">All models</option>
            {view?.models.map((m) => (
              <option key={m.key} value={m.key}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : (
        view && (
          <>
            <div className="metrics">
              <Metric
                label="Total tokens"
                value={<NumberValue value={view.summary.total_tokens} />}
                detail="Input and output tokens."
              />
              <Metric
                label="Input / output"
                value={
                  <>
                    <NumberValue value={view.summary.input_tokens} />
                    <span className={styles.divider}>/</span>
                    <NumberValue value={view.summary.output_tokens} />
                  </>
                }
                detail={`${compact(view.summary.cache_read_tokens)} cache read · ${compact(view.summary.cache_write_tokens)} cache write`}
              />
              <Metric
                label="Model requests"
                value={<NumberValue value={view.summary.model_requests} />}
                detail="Recorded model requests."
              />
              <Metric
                label="Estimated cost"
                value={money(view.summary.model_cost_usd)}
                detail={
                  view.summary.unknown_model_costs
                    ? `Partial · ${exact(view.summary.unknown_model_costs)} unpriced requests`
                    : "Estimate at model prices, separate from subscription charges."
                }
              />
            </div>
            <div className="split">
              <Panel
                title="Daily consumption"
                actions={
                  <div className="segmented">
                    <button
                      aria-pressed={metric === "tokens"}
                      onClick={() => setMetric("tokens")}
                    >
                      Tokens
                    </button>
                    <button
                      aria-pressed={metric === "cost"}
                      onClick={() => setMetric("cost")}
                    >
                      Cost
                    </button>
                  </div>
                }
              >
                {view.summary.model_requests === 0 ? (
                  <Empty title="No usage in this period" />
                ) : (
                  <Trend days={view.daily} metric={metric} />
                )}
              </Panel>
              <Panel
                title={
                  group === "model"
                    ? "Model distribution"
                    : "Thread consumption"
                }
                actions={
                  <select
                    aria-label="Group consumption"
                    value={group}
                    onChange={(e) => update({ group: e.target.value })}
                  >
                    <option value="model">By model</option>
                    <option value="thread">By thread</option>
                  </select>
                }
              >
                {view.groups.length === 0 ? (
                  <Empty title="No matching records" />
                ) : (
                  <div className={styles.ranking}>
                    {view.groups.map((item) => (
                      <div className={styles.rank} key={item.key}>
                        <div className={styles.rankLabel}>
                          {item.thread_id ? (
                            <Link
                              to={`/runs/${encodeURIComponent(item.thread_id)}`}
                            >
                              {item.label}
                            </Link>
                          ) : (
                            <button
                              className={styles.modelLink}
                              onClick={() =>
                                update({ model: item.key, group: "thread" })
                              }
                            >
                              {item.label}
                            </button>
                          )}
                          <NumberValue value={item.totals.total_tokens} />
                        </div>
                        <div className={styles.track}>
                          <span
                            style={{
                              width: `${view.summary.total_tokens ? (item.totals.total_tokens / view.summary.total_tokens) * 100 : 0}%`,
                            }}
                          />
                        </div>
                      </div>
                    ))}
                    {view.group_count > view.groups.length && (
                      <p className="hint">
                        Outside this page:{" "}
                        {exact(
                          Math.max(
                            0,
                            view.summary.total_tokens -
                              view.groups.reduce(
                                (sum, item) => sum + item.totals.total_tokens,
                                0,
                              ),
                          ),
                        )}{" "}
                        tokens
                      </p>
                    )}
                  </div>
                )}
                <div className="pagination">
                  <span>{view.group_count} groups</span>
                  <IconButton
                    icon="left"
                    label="Previous"
                    disabled={offset === 0 || query.isFetching}
                    onClick={() =>
                      update({ offset: String(Math.max(0, offset - 10)) })
                    }
                  />
                  <IconButton
                    icon="right"
                    label="Next"
                    disabled={
                      offset + 10 >= view.group_count || query.isFetching
                    }
                    onClick={() => update({ offset: String(offset + 10) })}
                  />
                </div>
              </Panel>
            </div>
            <div className="toolbar hint">
              <span>Updated {timestamp(view.as_of)}</span>
              <Info label="Usage details">
                UTC: {view.start} to {view.end} (exclusive). Projects follow
                current Thread assignments.
              </Info>
            </div>
          </>
        )
      )}
      <Allowances />
    </>
  );
}
function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: React.ReactNode;
  detail: string;
}) {
  return (
    <div className="panel metric">
      <div className="metric-label">
        {label}
        <Info label={`${label} details`}>{detail}</Info>
      </div>
      <div className="metric-value">{value}</div>
      {detail.startsWith("Partial") && <p className="hint">{detail}</p>}
    </div>
  );
}
export function Trend({
  days,
  metric,
}: {
  days: Schema["UsageDay"][];
  metric: "tokens" | "cost";
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const amount = (day: Schema["UsageDay"]) =>
    metric === "tokens"
      ? day.totals.total_tokens
      : Number(day.totals.model_cost_usd ?? 0);
  const maximum = Math.max(1, ...days.map(amount));
  const active = days.find((day) => day.day === selected);
  return (
    <div className={styles.trend}>
      <div className={styles.bars} aria-label={`Daily ${metric} in UTC`}>
        {days.map((day) => (
          <button
            key={day.day}
            className={styles.day}
            onClick={() => setSelected(day.day)}
            aria-pressed={selected === day.day}
            aria-label={`${day.day}: ${metric === "tokens" ? exact(day.totals.total_tokens) + " tokens" : money(day.totals.model_cost_usd) + (day.totals.unknown_model_costs ? ", partial cost" : "")}`}
          >
            <span
              className={styles.bar}
              style={{
                height: `${Math.max(1, (amount(day) / maximum) * 100)}%`,
              }}
            />
          </button>
        ))}
      </div>
      <div className={styles.axis}>
        <span>{days[0]?.day}</span>
        <span>{days.at(-1)?.day}</span>
      </div>
      <p className={styles.readout}>
        {active
          ? `${active.day} · ${exact(active.totals.total_tokens)} tokens · ${money(active.totals.model_cost_usd)}${active.totals.unknown_model_costs ? " (partial cost)" : ""}`
          : ""}
      </p>
    </div>
  );
}
