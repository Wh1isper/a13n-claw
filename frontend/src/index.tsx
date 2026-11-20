import {
  Button,
  DetailHeader,
  DetailPage,
  Empty,
  ErrorPage,
  FormField,
  Link,
  Loading,
  Page,
  React,
  ResourceTable,
  Section,
  Sections,
  Textarea,
  bridgeVersion,
  useAccess,
  useApp,
  type AppContribution,
} from "@a13n/console";
import {
  appPath,
  clawPath,
  url,
  useRead,
  useSelection,
  useWrite,
  type Admission,
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
  Icon,
  Pager,
  ResourceLink,
  Status,
  ThreadLink,
} from "./primitives";
import { ConfigurationPage, ConnectorsPage, SchedulesPage } from "./settings";
import styles from "./app.module.css";

function Dashboard() {
  return (
    <ClawScope>
      {(claw, picker) => (
        <DashboardBody key={claw.id} claw={claw} picker={picker} />
      )}
    </ClawScope>
  );
}
function SendToInbox({ claw }: { claw: string }) {
  const send = useWrite<Admission>();
  const [content, setContent] = React.useState("");
  const request = React.useRef<{ content: string; key: string } | null>(null);
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        const message = content.trim();
        if (!message || send.isPending) return;
        if (request.current?.content !== message)
          request.current = { content: message, key: crypto.randomUUID() };
        send.mutate(
          {
            path: `${clawPath(claw)}/input`,
            method: "POST",
            body: { content: message },
            key: request.current.key,
          },
          {
            onSuccess: () => {
              setContent("");
              request.current = null;
            },
          },
        );
      }}
    >
      <FormField label="Message" hideLabel>
        <Textarea
          value={content}
          rows={3}
          maxLength={65536}
          required
          disabled={send.isPending}
          placeholder="What should Main work on?"
          onChange={(event) => {
            setContent(event.currentTarget.value);
            send.reset();
          }}
        />
      </FormField>
      <div className={styles.actions}>
        <Button type="submit" disabled={!content.trim() || send.isPending}>
          {send.isPending ? "Sending…" : "Send"}
        </Button>
        {send.isSuccess && (
          <span className={styles.muted} role="status">
            Added to Inbox
          </span>
        )}
      </div>
      {send.isError && (
        <p className={styles.error} role="alert">
          {send.error.message}
        </p>
      )}
    </form>
  );
}
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

function ClawUsage({ claw }: { claw: string }) {
  const [days, setDays] = React.useState("7");
  const query = useRead<ModelUsage[]>(url(`${clawPath(claw)}/usage`, { days }));
  const currency = new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 4,
  });
  return (
    <Section
      title="Model usage"
      description="Recorded model requests for this Claw."
      actions={
        <select
          aria-label="Usage period"
          className={styles.select}
          value={days}
          onChange={(event) => setDays(event.currentTarget.value)}
        >
          <option value="7">Last 7 days</option>
          <option value="30">Last 30 days</option>
          <option value="90">Last 90 days</option>
        </select>
      }
    >
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPage error={query.error} />
      ) : query.data.length ? (
        <ResourceTable
          caption="Model usage"
          items={query.data.map((row) => ({
            ...row,
            id: row.model_id ?? "unattributed",
          }))}
          columns={[
            {
              label: "Model",
              tone: "primary",
              render: (row) => row.model ?? "Unattributed",
            },
            {
              label: "Requests",
              render: (row) => row.usage.requests.toLocaleString(),
            },
            {
              label: "Tokens",
              render: (row) =>
                (
                  row.usage.input_tokens + row.usage.output_tokens
                ).toLocaleString(),
            },
            {
              label: "Spend",
              render: (row) =>
                row.usage.cost === null ? (
                  "Unpriced"
                ) : (
                  <>
                    {currency.format(Number(row.usage.cost))}
                    {row.usage.unpriced_requests > 0 && (
                      <span className={styles.muted}> · partial</span>
                    )}
                  </>
                ),
            },
          ]}
        />
      ) : (
        <Empty
          title="No model usage in this period"
          description="Model requests appear here after they are recorded."
        />
      )}
    </Section>
  );
}
function DashboardBody({
  claw,
  picker,
}: {
  claw: Claw;
  picker: React.ReactNode;
}) {
  const { can } = useAccess();
  const overview = useRead<Overview>(clawPath(claw.id), 10000);
  const [workerPages, setWorkerPages] = React.useState<string[]>([]);
  const workers = useRead<Collection<Worker>>(
    url(`${clawPath(claw.id)}/workers`, { cursor: workerPages.at(-1) }),
    workerPages.length ? undefined : 10000,
  );
  const deliveries = useRead<Collection<Delivery>>(
    `${clawPath(claw.id)}/deliveries?limit=5`,
    10000,
  );
  if (overview.isPending) return <Loading page />;
  if (overview.isError) return <ErrorPage error={overview.error} />;
  const { main, read, unread, claw: saved, project } = overview.data;
  return (
    <Page
      title={saved.name}
      description="Incoming work, Main, and its shared Project."
      titleAction={
        <div className={styles.actions}>
          {picker}
          <ClawLink id={claw.id} page="settings">
            <span title="Configure Claw" aria-label="Configure Claw">
              <Icon name="settings" />
            </span>
          </ClawLink>
        </div>
      }
    >
      <Sections>
        <div className={styles.summary}>
          <section>
            <span className={styles.meta}>Unread Inbox</span>
            <strong>{unread}</strong>
            <span className={styles.muted}>
              of {saved.unread_limit.toLocaleString()} slots
            </span>
          </section>
          <section>
            <span className={styles.meta}>Read history</span>
            <strong>{read}</strong>
            <span className={styles.muted}>retained in Claw</span>
          </section>
          <section>
            <span className={styles.meta}>Main</span>
            <div className={styles.mainStatus}>
              <Status
                state={
                  main?.current_run_id
                    ? "active"
                    : main?.last_status === "waiting"
                      ? "waiting"
                      : main
                        ? "idle"
                        : "not started"
                }
              />
            </div>
            <ThreadLink
              thread={saved.main_thread_id}
              session={saved.main_session_id}
            >
              Open Thread
            </ThreadLink>
          </section>
        </div>
        {can("run") && (
          <Section title="Send to Inbox">
            <SendToInbox claw={claw.id} />
          </Section>
        )}
        <Section
          title="Recent deliveries"
          description="Messages arriving from connectors, workers, schedules, and Console."
          actions={
            <ClawLink id={claw.id} page="deliveries">
              All deliveries <Icon name="next" />
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
          title="Managed workers"
          description="Independent Threads working in the same Project."
        >
          {workers.isPending ? (
            <Loading />
          ) : workers.isError ? (
            <ErrorPage error={workers.error} />
          ) : workers.data.items.length ? (
            <ResourceTable
              caption="Managed workers"
              items={workers.data.items.map((row) => ({
                ...row,
                id: row.thread_id,
              }))}
              columns={[
                {
                  label: "Worker",
                  tone: "primary",
                  render: (row) => (
                    <ThreadLink thread={row.thread_id} session={row.session_id}>
                      {row.name}
                    </ThreadLink>
                  ),
                },
                {
                  label: "Activity",
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
                  label: "Last Run",
                  render: (row) =>
                    row.activity?.last_run_id ? (
                      <ThreadLink
                        thread={row.thread_id}
                        session={row.session_id}
                        run={row.activity.last_run_id}
                      >
                        {row.activity.last_status ?? "Inspect"}
                      </ThreadLink>
                    ) : (
                      "—"
                    ),
                },
              ]}
            />
          ) : (
            <Empty
              title="No workers yet"
              description="Workers appear here when Main delegates an assignment."
            />
          )}
          {workers.data &&
            (workers.data.next_cursor || workerPages.length > 0) && (
              <Pager
                next={!!workers.data.next_cursor}
                onPrevious={
                  workerPages.length
                    ? () => setWorkerPages((pages) => pages.slice(0, -1))
                    : undefined
                }
                onNext={() => {
                  const cursor = workers.data.next_cursor;
                  if (cursor) setWorkerPages((pages) => [...pages, cursor]);
                }}
              />
            )}
        </Section>
        <ClawUsage claw={claw.id} />
        <Section
          title="Project"
          description="Inspect the resources and execution facts in Console."
        >
          <ResourceTable
            caption="Project readiness"
            items={[
              {
                id: "env",
                name: "Environment",
                state: project.environment,
                path: "environments/instances",
              },
              ...(project.browser
                ? [
                    {
                      id: "browser",
                      name: "Browser",
                      state: project.browser,
                      path: "browsers",
                    },
                  ]
                : []),
              {
                id: "account",
                name: "Automation account",
                state: project.automation_account,
              },
              {
                id: "gh",
                name: "GitHub CLI account",
                state: project.cli?.github ?? "not checked",
                checked: project.cli_checked_at,
              },
              {
                id: "lark",
                name: "Lark CLI account",
                state: project.cli?.lark ?? "not checked",
                checked: project.cli_checked_at,
              },
            ]}
            columns={[
              {
                label: "Resource",
                tone: "primary",
                render: (row) =>
                  row.path ? (
                    <ResourceLink path={row.path}>{row.name}</ResourceLink>
                  ) : (
                    row.name
                  ),
              },
              {
                label: "Status",
                render: (row) => <Status state={row.state} />,
              },
              {
                label: "Last CLI check",
                render: (row) => <DateTime value={row.checked ?? null} />,
              },
            ]}
          />
          <div className={styles.references}>
            <ResourceLink path={`agents/${saved.agent_id}`}>Agent</ResourceLink>
            <ResourceLink path={`memory-spaces/${saved.memory_space_id}`}>
              Memory Space
            </ResourceLink>
            <ResourceLink path="usage">Usage</ResourceLink>
            {main?.last_run_id && (
              <ThreadLink
                session={saved.main_session_id}
                thread={saved.main_thread_id}
                run={main.last_run_id}
              >
                Last Main Run
              </ThreadLink>
            )}
          </div>
        </Section>
      </Sections>
    </Page>
  );
}

const sourceLabel = (origin: string) =>
  ({
    github: "GitHub",
    lark: "Lark",
    worker: "Worker",
    schedule: "Schedule",
    input: "Console",
    console: "Console",
  })[origin] ?? origin;
function DeliveryTable({ rows, claw }: { rows: Delivery[]; claw: string }) {
  const { workspaceId } = useApp();
  if (!rows.length)
    return (
      <Empty
        icon={<Icon name="inbox" />}
        title="No deliveries"
        description="New Inbox activity will appear here."
      />
    );
  return (
    <ResourceTable
      caption="Deliveries"
      items={rows}
      columns={[
        {
          label: "Source",
          tone: "primary",
          render: (row) => (
            <Link
              className={styles.link}
              to={url(appPath(workspaceId!, "deliveries"), {
                claw,
                delivery: row.id,
              })}
            >
              {sourceLabel(row.origin)}
              <Icon name="next" />
            </Link>
          ),
        },
        { label: "Admission", render: (row) => <Status state={row.status} /> },
        {
          label: "Received",
          render: (row) => <DateTime value={row.created_at} />,
        },
        {
          label: "Execution",
          render: (row) =>
            row.thread_id ? (
              <ThreadLink
                session={row.session_id}
                thread={row.thread_id}
                run={row.run_id}
              >
                {row.run_id ? "Run" : "Main"}
              </ThreadLink>
            ) : (
              "—"
            ),
        },
      ]}
    />
  );
}
function Deliveries() {
  return (
    <ClawScope>
      {(claw, picker) => (
        <DeliveriesBody key={claw.id} claw={claw} picker={picker} />
      )}
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
  const { params, select } = useSelection();
  const [pages, setPages] = React.useState<string[]>([]);
  const detail = params.get("delivery");
  const status = params.get("status");
  const rows = useRead<Collection<Delivery>>(
    url(`${clawPath(claw.id)}/deliveries`, { status, cursor: pages.at(-1) }),
    !detail && !pages.length ? 10000 : undefined,
  );
  if (detail) return <DeliveryRecord claw={claw} id={detail} />;
  return (
    <Page
      title="Deliveries"
      description="The latest 3,000 arrivals, up to seven days of history."
      titleAction={picker}
      toolbar={
        <select
          aria-label="Admission status"
          className={styles.select}
          value={status ?? ""}
          onChange={(event) => {
            setPages([]);
            select({ status: event.target.value || null });
          }}
        >
          <option value="">All statuses</option>
          <option value="accepted">Accepted</option>
          <option value="full">Inbox full</option>
          <option value="dropped">Dropped</option>
        </select>
      }
    >
      {rows.isPending ? (
        <Loading />
      ) : rows.isError ? (
        <ErrorPage error={rows.error} />
      ) : (
        <>
          <DeliveryTable rows={rows.data.items} claw={claw.id} />
          <Pager
            next={!!rows.data.next_cursor}
            onNext={() => setPages([...pages, rows.data.next_cursor!])}
            onPrevious={
              pages.length ? () => setPages(pages.slice(0, -1)) : undefined
            }
          />
        </>
      )}
    </Page>
  );
}
function DeliveryRecord({ claw, id }: { claw: Claw; id: string }) {
  const { workspaceId } = useApp();
  const record = useRead<DeliveryDetail>(
    `${clawPath(claw.id)}/deliveries/${id}`,
    10000,
  );
  const back = url(appPath(workspaceId!, "deliveries"), { claw: claw.id });
  if (record.isPending) return <Loading page />;
  if (record.isError)
    return (
      <Page
        title="Delivery"
        titleAction={<Link to={back}>Back to deliveries</Link>}
      >
        <ErrorPage error={record.error} />
      </Page>
    );
  const row = record.data;
  const source = row.source;
  const sourceThread =
    typeof source?.thread_id === "string" ? source.thread_id : null;
  const sourceSession =
    typeof source?.session_id === "string" ? source.session_id : null;
  const sourceRun = typeof source?.run_id === "string" ? source.run_id : null;
  return (
    <DetailPage
      back={back}
      backLabel="Deliveries"
      header={
        <DetailHeader
          name={`${sourceLabel(row.origin)} delivery`}
          status={<Status state={row.status} />}
        />
      }
    >
      <Sections>
        <Section title="Activity">
          <dl className={styles.facts}>
            <dt>Delivery</dt>
            <dd>{row.id}</dd>
            <dt>Received</dt>
            <dd>
              <DateTime value={row.created_at} />
            </dd>
            <dt>Inbox</dt>
            <dd>
              {row.item_id ? (
                <Status
                  state={
                    row.read_at
                      ? "read"
                      : row.content === null
                        ? "expired"
                        : "unread"
                  }
                />
              ) : (
                "—"
              )}
            </dd>
            <dt>Read at</dt>
            <dd>
              <DateTime value={row.read_at} />
            </dd>
          </dl>
          <div className={styles.references}>
            <ThreadLink thread={row.thread_id} session={row.session_id}>
              {"Main Thread"}
            </ThreadLink>
            {row.run_id && (
              <ThreadLink
                thread={row.thread_id}
                session={row.session_id}
                run={row.run_id}
              >
                {"Inspect Main Run"}
              </ThreadLink>
            )}
            <ThreadLink
              thread={sourceThread}
              session={sourceSession}
              run={sourceRun}
            >
              {"Inspect source"}
            </ThreadLink>
          </div>
        </Section>
        <Section title="Message">
          {row.content === null ? (
            <p className={styles.muted}>
              {row.item_id
                ? "Message content has expired."
                : "No Inbox item was created."}
            </p>
          ) : (
            <pre className={styles.payload}>{row.content}</pre>
          )}
        </Section>
        {row.source && (
          <Section title="Source metadata">
            <pre className={styles.payload}>
              {JSON.stringify(row.source, null, 2)}
            </pre>
          </Section>
        )}
      </Sections>
    </DetailPage>
  );
}

export default {
  bridge: bridgeVersion,
  pages: [
    {
      id: "overview",
      label: { en: "Claw", "zh-CN": "Claw" },
      component: Dashboard,
    },
    {
      id: "deliveries",
      label: { en: "Deliveries", "zh-CN": "投递记录" },
      component: Deliveries,
    },
    {
      id: "connectors",
      label: { en: "Claw connectors", "zh-CN": "Claw 连接器" },
      component: ConnectorsPage,
    },
    {
      id: "schedules",
      label: { en: "Claw schedules", "zh-CN": "Claw 计划" },
      component: SchedulesPage,
    },
    {
      id: "settings",
      label: { en: "Claw settings", "zh-CN": "Claw 设置" },
      component: ConfigurationPage,
      permission: "admin",
    },
  ],
} satisfies AppContribution;
