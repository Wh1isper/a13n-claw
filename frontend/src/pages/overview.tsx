import {
  ChoiceField,
  Button,
  Link,
  ListRow,
  ListRows,
  SegmentedControl,
  DetailHeader,
  DetailLayout,
  DetailPage,
  Empty,
  ErrorPage,
  Loading,
  Page,
  Rail,
  RailRow,
  RailSection,
  React,
  ResourceTable,
  Section,
  Sections,
  Toolbar,
  bridgeVersion,
  useAccess,
  useApp,
  type AppContribution,
  type DetailTab,
} from "@a13n/console";
import {
  appPath,
  clawPath,
  url,
  threadPath,
  useRead,
  useSelection,
  type Claw,
  type Collection,
  type Delivery,
  type DeliveryDetail,
  type Overview,
  type Worker,
} from "../data";
import {
  ClawLink,
  ClawScope,
  DateTime,
  Pager,
  ResourceLink,
  Status,
  ThreadLink,
} from "../primitives";

import { useCopy } from "../copy";
import styles from "../app.module.css";
import { Icon } from "../ui/icons";

import { DeliveryTable } from "./deliveries";

type ModelUsage = {
  model_id: string | null;
  model: string | null;
  usage: {
    requests: number;
    input_tokens: number;
    output_tokens: number;
    cost: string | null;
    unpriced_requests: number;
  };
};

export function compactNumber(value: number, language = "en") {
  return Intl.NumberFormat(language, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

function ClawUsage({ claw }: { claw: string }) {
  const { copy, language } = useCopy();
  const [days, setDays] = React.useState("7");
  const query = useRead<ModelUsage[]>(url(`${clawPath(claw)}/usage`, { days }));
  const count = new Intl.NumberFormat(language);
  const money = new Intl.NumberFormat(language, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 4,
  });
  const shortMoney = new Intl.NumberFormat(language, {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 3,
  });
  const rows = query.data ?? [];
  const totals = rows.reduce(
    (acc, { usage }) => ({
      requests: acc.requests + usage.requests,
      tokens: acc.tokens + usage.input_tokens + usage.output_tokens,
      cost:
        usage.cost === null ? acc.cost : (acc.cost ?? 0) + Number(usage.cost),
      unpriced: acc.unpriced + usage.unpriced_requests,
    }),
    { requests: 0, tokens: 0, cost: null as number | null, unpriced: 0 },
  );
  const labels = {
    requests: copy("Requests", "请求数"),
    tokens: copy("Tokens", "令牌数"),
    spend: copy("Spend", "费用"),
    unpriced: copy("Unpriced", "未计价"),
    partial: copy("Partial pricing", "部分计价"),
  };
  return (
    <Section
      title={copy("Usage", "用量")}
      description={copy(
        "Recorded model usage for this Claw.",
        "当前 Claw 已记录的模型用量。",
      )}
      actions={
        <div className={styles.actions}>
          <SegmentedControl
            label={copy("Usage period", "用量周期")}
            size="sm"
            value={days}
            onValueChange={setDays}
            options={[
              { value: "7", label: copy("7d", "7天") },
              { value: "30", label: copy("30d", "30天") },
              { value: "90", label: copy("90d", "90天") },
            ]}
          />
          <ResourceLink path="usage">
            {copy("Open Usage", "打开用量")}
          </ResourceLink>
        </div>
      }
    >
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPage error={query.error} />
      ) : !rows.length ? (
        <p className={styles.muted}>
          {copy(
            "No model usage recorded in this period.",
            "此周期内尚无模型用量记录。",
          )}
        </p>
      ) : (
        <>
          <div className={styles.metrics}>
            <section>
              <span className={styles.meta}>{labels.requests}</span>
              <strong title={count.format(totals.requests)}>
                {compactNumber(totals.requests, language)}
              </strong>
            </section>
            <section>
              <span className={styles.meta}>{labels.tokens}</span>
              <strong title={count.format(totals.tokens)}>
                {compactNumber(totals.tokens, language)}
              </strong>
            </section>
            <section>
              <span className={styles.meta}>{labels.spend}</span>
              <strong
                title={
                  totals.cost === null ? undefined : money.format(totals.cost)
                }
              >
                {totals.cost === null
                  ? labels.unpriced
                  : shortMoney.format(totals.cost)}
              </strong>
              {totals.cost !== null && totals.unpriced > 0 && (
                <span className={styles.muted}>{labels.partial}</span>
              )}
            </section>
          </div>
          <ResourceTable
            caption={copy("Usage by model", "按模型查看用量")}
            items={rows.map((row) => ({
              ...row,
              id: row.model_id ?? "unattributed",
            }))}
            columns={[
              {
                label: copy("Model", "模型"),
                tone: "primary",
                render: (row) => row.model ?? copy("Unattributed", "未归属"),
              },
              {
                label: copy("Share", "占比"),
                render: (row) => {
                  const tokens =
                    row.usage.input_tokens + row.usage.output_tokens;
                  const share = totals.tokens
                    ? (tokens / totals.tokens) * 100
                    : totals.requests
                      ? (row.usage.requests / totals.requests) * 100
                      : 0;
                  return (
                    <div className={styles.share}>
                      <span className={styles.shareTrack}>
                        <span style={{ width: `${share}%` }} />
                      </span>
                      <span>{share.toFixed(1)}%</span>
                    </div>
                  );
                },
              },
              {
                label: labels.requests,
                align: "right",
                render: (row) => (
                  <span title={count.format(row.usage.requests)}>
                    {compactNumber(row.usage.requests, language)}
                  </span>
                ),
              },
              {
                label: labels.tokens,
                align: "right",
                render: (row) => {
                  const tokens =
                    row.usage.input_tokens + row.usage.output_tokens;
                  return (
                    <span title={count.format(tokens)}>
                      {compactNumber(tokens, language)}
                    </span>
                  );
                },
              },
              {
                label: labels.spend,
                align: "right",
                render: (row) =>
                  row.usage.cost === null
                    ? labels.unpriced
                    : `${money.format(Number(row.usage.cost))}${row.usage.unpriced_requests ? ` · ${labels.partial}` : ""}`,
              },
            ]}
          />
        </>
      )}
    </Section>
  );
}

export function Dashboard() {
  return (
    <ClawScope>
      {(claw, picker) => (
        <DashboardBody key={claw.id} claw={claw} picker={picker} />
      )}
    </ClawScope>
  );
}

function DashboardBody({
  claw,
  picker,
}: {
  claw: Claw;
  picker: React.ReactNode;
}) {
  const { copy, language } = useCopy();
  const { can } = useAccess();
  const { workspaceId } = useApp();
  const overview = useRead<Overview>(clawPath(claw.id), 10000);
  const [pages, setPages] = React.useState<string[]>([]);
  const workers = useRead<Collection<Worker>>(
    url(`${clawPath(claw.id)}/workers`, { cursor: pages.at(-1) }),
    10000,
  );
  const deliveries = useRead<Collection<Delivery>>(
    `${clawPath(claw.id)}/deliveries?limit=5`,
    10000,
  );
  if (overview.isPending) return <Loading page />;
  if (overview.isError) return <ErrorPage error={overview.error} />;
  const {
    main,
    read,
    unread,
    claw: saved,
    project,
    last_failure,
    agent_revision_outdated,
  } = overview.data;
  const settings = (text: string, project = false) =>
    can("admin") && (
      <Button
        variant="outline"
        size="sm"
        render={
          <Link
            to={url(appPath(workspaceId!, "settings"), {
              claw: claw.id,
              section: project ? "project" : null,
            })}
          />
        }
      >
        {text}
      </Button>
    );
  const run = main?.last_run_id &&
    saved.main_thread_id &&
    saved.main_session_id && (
      <Button
        variant="outline"
        size="sm"
        render={
          <Link
            to={threadPath(
              workspaceId!,
              saved.main_session_id,
              saved.main_thread_id,
              main.last_run_id,
            )}
          />
        }
      >
        {copy("Open Run", "打开运行")}
      </Button>
    );
  const missingEnvironment =
    ["missing", "lost", "failed", "deleted"].includes(project.environment) ||
    project.environment_failure?.permanent;
  const missingBrowser =
    project.browser &&
    ["missing", "lost", "failed", "deleted"].includes(project.browser);
  const mainState = main?.current_run_id
    ? "active"
    : main?.last_status === "waiting"
      ? "waiting"
      : main
        ? "idle"
        : "not started";
  return (
    <Page
      title={saved.name}
      description={copy(
        "Incoming work, Main, and its shared Project.",
        "查看传入工作、主线程和共享项目。",
      )}
      actions={
        <div className={`${styles.actions} ${styles.headerActions}`}>
          {picker}
          {can("admin") && (
            <Button
              variant="outline"
              size="sm"
              render={
                <Link
                  to={url(appPath(workspaceId!, "settings"), { claw: claw.id })}
                />
              }
            >
              <Icon name="settings" />
              {copy("Claw settings", "Claw 设置")}
            </Button>
          )}
          {saved.main_thread_id && saved.main_session_id && (
            <Button
              size="sm"
              render={
                <Link
                  to={threadPath(
                    workspaceId!,
                    saved.main_session_id,
                    saved.main_thread_id,
                  )}
                />
              }
            >
              <Icon name="external" />
              {copy("Open Main Thread", "打开主线程")}
            </Button>
          )}
        </div>
      }
    >
      <Sections>
        <div className={styles.attentionList}>
          {main?.last_status === "failed" && (
            <Attention
              severity="error"
              title={copy("Latest Main Run failed", "最近一次主线程运行失败")}
              action={run}
            >
              {last_failure?.message ||
                copy(
                  "Inspect the Run for failure details.",
                  "请打开运行查看失败详情。",
                )}
            </Attention>
          )}
          {missingEnvironment && (
            <Attention
              severity="error"
              title={copy(
                "Project Environment needs replacement",
                "项目环境需要更换",
              )}
              action={settings(
                copy("Open Project settings", "打开项目设置"),
                true,
              )}
            >
              {project.environment_failure?.message ||
                copy(
                  "Select a usable Environment in Project settings.",
                  "请在项目设置中选择可用的环境。",
                )}
            </Attention>
          )}
          {missingBrowser && (
            <Attention
              severity="error"
              title={copy(
                "Project Browser needs replacement",
                "项目浏览器需要更换",
              )}
              action={settings(
                copy("Open Project settings", "打开项目设置"),
                true,
              )}
            >
              {project.browser_failure ||
                copy(
                  "Select a usable Browser in Project settings.",
                  "请在项目设置中选择可用的浏览器。",
                )}
            </Attention>
          )}
          {!main?.current_run_id && main?.last_status === "waiting" && (
            <Attention
              severity="warning"
              title={copy("Main needs a decision", "主线程等待处理")}
              action={run}
            >
              {copy(
                "Answer the pending question or approval in the native Run.",
                "请在原生运行页面回答问题或处理审批。",
              )}
            </Attention>
          )}
          {project.environment === "not configured" && (
            <Attention
              title={copy("Connect a Project Environment", "绑定项目环境")}
              action={settings(copy("Set up Project", "设置项目"), true)}
            >
              {copy(
                "The Inbox can receive work. Main starts once an Environment is available.",
                "收件箱可以接收工作；环境可用后主线程才能启动。",
              )}
            </Attention>
          )}
          {project.environment === "stopped" && !missingEnvironment && (
            <Attention
              title={copy("Project Environment is stopped", "项目环境已停止")}
              action={
                <Button
                  variant="outline"
                  size="sm"
                  render={
                    <Link
                      to={`/workspace/${workspaceId}/environments/instances`}
                    />
                  }
                >
                  {copy("Open Environment", "打开环境")}
                </Button>
              }
            >
              {copy(
                "Resume the same instance to keep its files and working state.",
                "启动同一实例，以继续使用原有文件和工作状态。",
              )}
            </Attention>
          )}
          {agent_revision_outdated && can("admin") && (
            <Attention
              severity="info"
              title={copy(
                "New default Agent revision",
                "有新的默认智能体修订版",
              )}
              action={settings(
                copy("Review Claw settings", "查看 Claw 设置"),
                true,
              )}
            >
              {copy(
                "Sync the revision to use it for future Runs.",
                "同步后，后续运行将使用该修订版。",
              )}
            </Attention>
          )}
        </div>
        <div className={styles.summary}>
          <section>
            <span className={styles.meta}>{copy("Main", "主线程")}</span>
            <div className={styles.mainStatus}>
              <Status state={mainState} />
            </div>
            <ThreadLink
              thread={saved.main_thread_id}
              session={saved.main_session_id}
            >
              {copy("Open Thread", "打开线程")}
            </ThreadLink>
          </section>
          <section>
            <span className={styles.meta}>
              {copy("Unread Inbox", "未读收件")}
            </span>
            <strong>{unread.toLocaleString(language)}</strong>
            <ClawLink
              id={claw.id}
              page="deliveries"
              query={{ inbox: "unread" }}
            >
              {copy("View unread", "查看未读")}
            </ClawLink>
          </section>
          <section>
            <span className={styles.meta}>
              {copy("Read history", "已读记录")}
            </span>
            <strong>{read.toLocaleString(language)}</strong>
            <ClawLink id={claw.id} page="deliveries" query={{ inbox: "read" }}>
              {copy("View read", "查看已读")}
            </ClawLink>
          </section>
        </div>
        <ClawUsage claw={claw.id} />
        <Section
          title={copy("Recent deliveries", "最近投递")}
          actions={
            <ClawLink id={claw.id} page="deliveries">
              {copy("All deliveries", "全部投递")}
            </ClawLink>
          }
        >
          {deliveries.isPending ? (
            <Loading />
          ) : deliveries.isError ? (
            <ErrorPage error={deliveries.error} />
          ) : (
            <DeliveryTable rows={deliveries.data.items} claw={claw.id} />
          )}
        </Section>
        <Section
          title={copy("Managed workers", "受管工作线程")}
          description={copy(
            "Independent Threads working in the same Project.",
            "在同一项目中独立工作的线程。",
          )}
        >
          {workers.isPending ? (
            <Loading />
          ) : workers.isError ? (
            <ErrorPage error={workers.error} />
          ) : !workers.data.items.length ? (
            <p className={styles.muted}>
              {copy("No workers yet.", "还没有工作线程。")}
            </p>
          ) : (
            <>
              <ResourceTable
                caption={copy("Managed workers", "受管工作线程")}
                items={workers.data.items.map((row) => ({
                  ...row,
                  id: row.thread_id,
                }))}
                columns={[
                  {
                    label: copy("Worker", "工作线程"),
                    tone: "primary",
                    render: (row) => (
                      <ThreadLink
                        thread={row.thread_id}
                        session={row.session_id}
                      >
                        {row.name}
                      </ThreadLink>
                    ),
                  },
                  {
                    label: copy("Activity", "活动"),
                    render: (row) => (
                      <Status
                        state={
                          row.activity?.current_run_id
                            ? "active"
                            : row.activity?.last_status === "waiting"
                              ? "waiting"
                              : "idle"
                        }
                      />
                    ),
                  },
                  {
                    label: copy("Last status", "最近状态"),
                    render: (row) =>
                      row.activity?.last_status ? (
                        <Status state={row.activity.last_status} />
                      ) : (
                        "—"
                      ),
                  },
                  {
                    label: copy("Last Run", "最近运行"),
                    render: (row) =>
                      row.activity?.last_run_id ? (
                        <ThreadLink
                          thread={row.thread_id}
                          session={row.session_id}
                          run={row.activity.last_run_id}
                        >
                          {copy("Open Run", "打开运行")}
                        </ThreadLink>
                      ) : (
                        "—"
                      ),
                  },
                ]}
              />
              <Pager
                nextCursor={workers.data.next_cursor}
                pages={pages}
                setPages={setPages}
              />
            </>
          )}
        </Section>
        <Section
          title={copy("Project resources", "项目资源")}
          actions={settings(copy("Manage in settings", "在设置中管理"), true)}
        >
          <ListRows>
            <ListRow
              icon={<Icon name="claw" />}
              name={copy("Agent", "智能体")}
              secondary={saved.agent_revision_id ?? "—"}
              control={agent_revision_outdated && <Status state="outdated" />}
              actions={
                <ResourceLink path={`agents/${saved.agent_id}`}>
                  {copy("Open", "打开")}
                </ResourceLink>
              }
            />
            <ListRow
              icon={<Icon name="terminal" />}
              name={copy("Environment", "环境")}
              secondary={
                project.environment_name ?? copy("Not configured", "未配置")
              }
              control={<Status state={project.environment} />}
              actions={
                saved.environment_id ? (
                  <ResourceLink path="environments/instances">
                    {copy("Open", "打开")}
                  </ResourceLink>
                ) : (
                  settings(copy("Configure", "配置"), true)
                )
              }
            />
            <ListRow
              icon={<Icon name="browser" />}
              name={copy("Browser", "浏览器")}
              secondary={
                project.browser_name ?? copy("Not configured", "未配置")
              }
              control={<Status state={project.browser ?? "not configured"} />}
              actions={
                saved.browser_id ? (
                  <ResourceLink path="browsers">
                    {copy("Open", "打开")}
                  </ResourceLink>
                ) : (
                  settings(copy("Configure", "配置"), true)
                )
              }
            />
            <ListRow
              icon={<Icon name="memory" />}
              name={copy("Memory Space", "记忆空间")}
              secondary={saved.memory_space_id}
              actions={
                <ResourceLink path={`memory-spaces/${saved.memory_space_id}`}>
                  {copy("Open", "打开")}
                </ResourceLink>
              }
            />
            <ListRow
              icon={<Icon name="claw" />}
              name={copy("Automation account", "自动化账号")}
              secondary={saved.service_account_id}
              control={
                project.automation_account && (
                  <Status state={project.automation_account} />
                )
              }
              actions={
                <ResourceLink path="settings/service-accounts">
                  {copy("Open", "打开")}
                </ResourceLink>
              }
            />
            <ListRow
              icon={<Icon name="terminal" />}
              name={copy("Platform CLIs", "平台命令行工具")}
              secondary={
                Object.entries(project.cli_versions ?? {})
                  .map(([name, version]) => `${name} ${version}`)
                  .join(" · ") || copy("Not checked", "尚未检查")
              }
              control={
                project.cli_update_state && (
                  <Status state={project.cli_update_state} />
                )
              }
              actions={settings(copy("Configure", "配置"), true)}
            />
          </ListRows>
        </Section>
      </Sections>
    </Page>
  );
}

function Attention({
  title,
  action,
  children,
  severity = "warning",
}: {
  severity?: "error" | "warning" | "info";
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <aside className={styles.attention} data-severity={severity}>
      <Icon
        name={
          severity === "error"
            ? "error"
            : severity === "info"
              ? "info"
              : "warning"
        }
      />
      <div>
        <strong>{title}</strong>
        <p>{children}</p>
      </div>
      {action}
    </aside>
  );
}
