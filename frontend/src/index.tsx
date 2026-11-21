import {
  ChoiceField,
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
  useRead,
  useSelection,
  type Claw,
  type Collection,
  type Delivery,
  type DeliveryDetail,
  type Overview,
  type Worker,
} from "./data";
import {
  ClawLink,
  ClawScope,
  DateTime,
  Pager,
  ResourceLink,
  Status,
  ThreadLink,
} from "./primitives";
import { ConfigurationPage, ConnectorsPage, SchedulesPage } from "./settings";
import { useCopy } from "./copy";
import styles from "./app.module.css";

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
          <ChoiceField
            label={copy("Usage period", "用量周期")}
            hideLabel
            value={days}
            onValueChange={setDays}
            options={[
              { value: "7", label: copy("Last 7 days", "最近 7 天") },
              { value: "30", label: copy("Last 30 days", "最近 30 天") },
              { value: "90", label: copy("Last 90 days", "最近 90 天") },
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
          <div className={styles.summary}>
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
                label: labels.requests,
                align: "right",
                render: (row) => count.format(row.usage.requests),
              },
              {
                label: labels.tokens,
                align: "right",
                render: (row) =>
                  count.format(
                    row.usage.input_tokens + row.usage.output_tokens,
                  ),
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

function Dashboard() {
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
  const settings = (text: string) =>
    can("admin") && (
      <ClawLink id={claw.id} page="settings">
        {text}
      </ClawLink>
    );
  const run = main?.last_run_id && (
    <ThreadLink
      thread={saved.main_thread_id}
      session={saved.main_session_id}
      run={main.last_run_id}
    >
      {copy("Open Run", "打开运行")}
    </ThreadLink>
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
      titleAction={
        <div className={styles.actions}>
          {picker}
          {settings(copy("Claw settings", "Claw 设置"))}
        </div>
      }
    >
      <Sections>
        {!main?.current_run_id && main?.last_status === "waiting" && (
          <Attention
            title={copy("Main needs a decision", "主线程等待处理")}
            action={run}
          >
            {copy(
              "Answer the pending question or approval in the native Run.",
              "请在原生运行页面回答问题或处理审批。",
            )}
          </Attention>
        )}
        {main?.last_status === "failed" && (
          <Attention
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
        {project.environment === "not configured" && (
          <Attention
            title={copy("Connect a Project Environment", "绑定项目环境")}
            action={settings(copy("Set up Project", "设置项目"))}
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
              <ResourceLink path="environments/instances">
                {copy("Open Environment", "打开环境")}
              </ResourceLink>
            }
          >
            {copy(
              "Resume the same instance to keep its files and working state.",
              "启动同一实例，以继续使用原有文件和工作状态。",
            )}
          </Attention>
        )}
        {missingEnvironment && (
          <Attention
            title={copy(
              "Project Environment needs replacement",
              "项目环境需要更换",
            )}
            action={settings(copy("Open Project settings", "打开项目设置"))}
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
            title={copy(
              "Project Browser needs replacement",
              "项目浏览器需要更换",
            )}
            action={settings(copy("Open Project settings", "打开项目设置"))}
          >
            {project.browser_failure ||
              copy(
                "Select a usable Browser in Project settings.",
                "请在项目设置中选择可用的浏览器。",
              )}
          </Attention>
        )}
        {project.unauthorized_reply_connectors > 0 && (
          <Attention
            title={copy(
              "Connector replies need reauthorization",
              "连接器回复需要重新授权",
            )}
            action={
              <ClawLink id={claw.id} page="connectors">
                {copy("Open Connectors", "打开连接器")}
              </ClawLink>
            }
          >
            {copy(
              "Authorize replies for the current Project Environment.",
              "为当前项目环境重新授权回复。",
            )}
          </Attention>
        )}
        {agent_revision_outdated && can("admin") && (
          <Attention
            title={copy("New default Agent revision", "有新的默认智能体修订版")}
            action={settings(copy("Review Claw settings", "查看 Claw 设置"))}
          >
            {copy(
              "Sync the revision to use it for future Runs.",
              "同步后，后续运行将使用该修订版。",
            )}
          </Attention>
        )}
        <div className={styles.summary}>
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
                    label: copy("Model", "模型"),
                    render: (row) =>
                      row.model ?? copy("Agent default", "智能体默认"),
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
        <Section title={copy("Project resources", "项目资源")}>
          <div className={styles.references}>
            <ResourceLink path={`agents/${saved.agent_id}`}>
              {copy("Agent", "智能体")}
            </ResourceLink>
            {saved.environment_id && (
              <ResourceLink path="environments/instances">
                {project.environment_name ?? copy("Environment", "环境")}
              </ResourceLink>
            )}
            {saved.browser_id && (
              <ResourceLink path="browsers">
                {project.browser_name ?? copy("Browser", "浏览器")}
              </ResourceLink>
            )}
            <ResourceLink path={`memory-spaces/${saved.memory_space_id}`}>
              {copy("Memory Space", "记忆空间")}
            </ResourceLink>
          </div>
        </Section>
      </Sections>
    </Page>
  );
}

function Attention({
  title,
  action,
  children,
}: {
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <aside className={styles.attention}>
      <div>
        <strong>{title}</strong>
        <p>{children}</p>
      </div>
      {action}
    </aside>
  );
}

function useSourceLabel() {
  const { copy } = useCopy();
  return (source: string) =>
    ({
      lark: copy("Lark / Feishu", "飞书 / Lark"),
      github: "GitHub",
      worker: copy("Worker", "工作线程"),
      schedule: copy("Schedule", "排程"),
      workspace: copy("Headless input", "无界面输入"),
      console: copy("Headless input", "无界面输入"),
    })[source] ?? source;
}

function inboxState(row: Delivery) {
  return row.item_id
    ? row.read_at
      ? "read"
      : row.kind
        ? "unread"
        : "expired"
    : null;
}

function DeliveryTable({ rows, claw }: { rows: Delivery[]; claw: string }) {
  const source = useSourceLabel();
  const { copy } = useCopy();
  return rows.length ? (
    <ResourceTable
      caption={copy("Deliveries", "投递记录")}
      items={rows}
      columns={[
        {
          label: copy("Source", "来源"),
          tone: "primary",
          render: (row) => (
            <ClawLink id={claw} page="deliveries" query={{ delivery: row.id }}>
              {source(row.origin)}
            </ClawLink>
          ),
        },
        {
          label: copy("Preview", "预览"),
          render: (row) => (
            <span className={styles.preview}>{row.preview ?? "—"}</span>
          ),
        },
        {
          label: copy("Admission", "接收结果"),
          render: (row) => <Status state={row.status} />,
        },
        {
          label: copy("Inbox state", "收件状态"),
          render: (row) => {
            const state = inboxState(row);
            return state ? <Status state={state} /> : "—";
          },
        },
        {
          label: copy("Received", "接收时间"),
          render: (row) => <DateTime value={row.created_at} />,
        },
        {
          label: copy("Execution", "执行"),
          render: (row) =>
            !row.thread_id ? (
              "—"
            ) : (
              <ThreadLink
                thread={row.thread_id}
                session={row.session_id}
                run={row.run_id}
              >
                {row.run_id
                  ? copy("Open Run", "打开运行")
                  : copy("Open Thread", "打开线程")}
              </ThreadLink>
            ),
        },
      ]}
    />
  ) : (
    <Empty
      title={copy("No matching deliveries", "没有匹配的投递")}
      description={copy(
        "Try another filter or check back after new work arrives.",
        "请更换筛选条件，或等待新投递到达。 ",
      )}
    />
  );
}

function Deliveries() {
  const { params } = useSelection();
  const id = params.get("delivery");
  return (
    <ClawScope>
      {(claw, picker) =>
        id ? (
          <DeliveryRecord key={id} claw={claw} id={id} />
        ) : (
          <DeliveriesBody key={claw.id} claw={claw} picker={picker} />
        )
      }
    </ClawScope>
  );
}

function DeliveriesBody({
  claw,
  picker,
}: {
  claw: Claw;
  picker: React.ReactNode;
}) {
  const { copy } = useCopy();
  const { params, select } = useSelection();
  const status = params.get("admission") ?? "";
  const inbox = params.get("inbox") ?? "";
  const [paging, setPaging] = React.useState({
    filter: "",
    pages: [] as string[],
  });
  const filter = `${status}:${inbox}`;
  const pages = paging.filter === filter ? paging.pages : [];
  const query = useRead<Collection<Delivery>>(
    url(`${clawPath(claw.id)}/deliveries`, {
      cursor: pages.at(-1),
      status,
      inbox_state: inbox,
    }),
    10000,
  );
  const all = { value: "all", label: copy("All", "全部") };
  return (
    <Page
      title={copy("Deliveries", "投递记录")}
      titleAction={picker}
      description={copy(
        "Inspect what arrived and whether Main opened it.",
        "查看收到的消息，以及主线程是否已打开。",
      )}
      toolbar={
        <Toolbar
          filters={
            <>
              <ChoiceField
                label={copy("Admission", "接收结果")}
                hideLabel
                variant="filter"
                value={status || "all"}
                onValueChange={(value) =>
                  select({ admission: value === "all" ? null : value })
                }
                options={[
                  all,
                  { value: "accepted", label: copy("Accepted", "已接收") },
                  { value: "full", label: copy("Inbox full", "收件箱已满") },
                  { value: "dropped", label: copy("Dropped", "已丢弃") },
                ]}
              />
              <ChoiceField
                label={copy("Inbox state", "收件状态")}
                hideLabel
                variant="filter"
                value={inbox || "all"}
                onValueChange={(value) =>
                  select({ inbox: value === "all" ? null : value })
                }
                options={[
                  { ...all, label: copy("Any Inbox state", "所有收件状态") },
                  { value: "unread", label: copy("Unread", "未读") },
                  { value: "read", label: copy("Read", "已读") },
                  { value: "expired", label: copy("Expired", "已过期") },
                ]}
              />
            </>
          }
        />
      }
    >
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPage error={query.error} />
      ) : (
        <>
          <DeliveryTable rows={query.data.items} claw={claw.id} />
          <Pager
            nextCursor={query.data.next_cursor}
            pages={pages}
            setPages={(pages) => setPaging({ filter, pages })}
          />
        </>
      )}
    </Page>
  );
}

function DeliveryRecord({ claw, id }: { claw: Claw; id: string }) {
  const { copy } = useCopy();
  const sourceLabel = useSourceLabel();
  const { workspaceId } = useApp();
  const { params } = useSelection();
  const [tab, setTab] = React.useState("message");
  const query = useRead<DeliveryDetail>(
    `${clawPath(claw.id)}/deliveries/${encodeURIComponent(id)}`,
    10000,
  );
  if (query.isPending) return <Loading page />;
  if (query.isError) return <ErrorPage error={query.error} />;
  const row = query.data;
  const state = inboxState(row);
  const back = url(appPath(workspaceId!, "deliveries"), {
    claw: claw.id,
    inbox: params.get("inbox"),
    admission: params.get("admission"),
  });
  const tabs: DetailTab[] = [
    { value: "message", label: copy("Message", "消息") },
  ];
  if (row.source)
    tabs.push({
      value: "source",
      label: copy("Source metadata", "来源元数据"),
    });
  const rail = (
    <Rail>
      <RailSection title={copy("Activity", "活动")}>
        <RailRow label={copy("Received", "接收时间")}>
          <DateTime value={row.created_at} />
        </RailRow>
        <RailRow label={copy("Inbox state", "收件状态")}>
          {state ? <Status state={state} /> : "—"}
        </RailRow>
        <RailRow label={copy("Read at", "打开时间")}>
          <DateTime value={row.read_at} />
        </RailRow>
        <ThreadLink thread={row.thread_id} session={row.session_id}>
          {copy("Open Thread", "打开线程")}
        </ThreadLink>
        {row.run_id && (
          <ThreadLink
            thread={row.thread_id}
            session={row.session_id}
            run={row.run_id}
          >
            {copy("Open Run", "打开运行")}
          </ThreadLink>
        )}
      </RailSection>
    </Rail>
  );
  return (
    <DetailPage
      back={back}
      backLabel={copy("Deliveries", "投递记录")}
      tabs={tabs}
      tab={row.source ? tab : "message"}
      onTabChange={setTab}
      header={
        <DetailHeader
          name={sourceLabel(row.origin)}
          resourceKey={row.id}
          status={<Status state={row.status} />}
        />
      }
    >
      <DetailLayout rail={rail}>
        {tab === "source" && row.source ? (
          <pre className={styles.payload}>
            {JSON.stringify(row.source, null, 2)}
          </pre>
        ) : row.content === null ? (
          <p className={styles.muted}>
            {row.item_id
              ? copy("Message content has expired.", "消息内容已过期。")
              : copy("No Inbox item was created.", "没有创建收件条目。")}
          </p>
        ) : (
          <pre className={styles.payload}>{row.content}</pre>
        )}
      </DetailLayout>
    </DetailPage>
  );
}

export default {
  bridge: bridgeVersion,
  pages: [
    { id: "overview", label: "Claw", component: Dashboard },
    {
      id: "deliveries",
      label: { en: "Deliveries", "zh-CN": "投递记录" },
      component: Deliveries,
    },
    {
      id: "connectors",
      label: { en: "Connectors", "zh-CN": "连接器" },
      component: ConnectorsPage,
    },
    {
      id: "schedules",
      label: { en: "Schedules", "zh-CN": "排程" },
      component: SchedulesPage,
    },
  ],
  settings: [
    {
      id: "settings",
      label: { en: "Claw settings", "zh-CN": "Claw 设置" },
      component: ConfigurationPage,
      permission: "admin",
      scope: "workspace",
    },
  ],
} satisfies AppContribution;
