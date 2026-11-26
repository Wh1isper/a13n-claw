import {
  ChoiceField,
  Button,
  CollectionFooter,
  SegmentedControl,
  useNavigate,
  useQuery,
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
  json,
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
import { CopyButton } from "../ui/copy-button";
import { Icon } from "../ui/icons";

function useSourceLabel() {
  const { copy } = useCopy();
  return (source: string) =>
    ({
      lark: copy("Lark / Feishu", "飞书 / Lark"),
      github: "GitHub",
      worker: copy("Worker", "工作线程"),
      schedule: copy("Schedule", "排程"),
      input: copy("Headless input", "无界面输入"),
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

export function DeliveryTable({
  rows,
  claw,
}: {
  rows: Delivery[];
  claw: string;
}) {
  const source = useSourceLabel();
  const { copy } = useCopy();
  const { workspaceId } = useApp();
  const navigate = useNavigate();
  const { params } = useSelection();
  const selection = {
    inbox: params.get("inbox"),
    admission: params.get("admission"),
    origin: params.get("origin"),
  };
  return rows.length ? (
    <ResourceTable
      caption={copy("Deliveries", "投递记录")}
      items={rows}
      onRowActivate={(row) =>
        navigate(
          url(appPath(workspaceId!, "deliveries"), {
            claw,
            ...selection,
            delivery: row.id,
          }),
        )
      }
      columns={[
        {
          label: copy("Source", "来源"),
          tone: "primary",
          render: (row) => (
            <ClawLink
              id={claw}
              page="deliveries"
              query={{ ...selection, delivery: row.id }}
            >
              {source(row.origin)}
            </ClawLink>
          ),
        },
        {
          label: copy("Preview", "预览"),
          render: (row) => (
            <span className={styles.preview} title={row.preview ?? undefined}>
              {row.preview ?? "—"}
            </span>
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

export function Deliveries() {
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
  const origin = params.get("origin") ?? "";
  const app = useApp();
  const [localPage, setLocalPage] = React.useState(0);
  const inbox = params.get("inbox") ?? "";
  const [paging, setPaging] = React.useState({
    filter: "",
    pages: [] as string[],
  });
  const filter = `${status}:${inbox}:${origin}`;
  const pages = paging.filter === filter ? paging.pages : [];
  const query = useRead<Collection<Delivery>>(
    origin
      ? null
      : url(`${clawPath(claw.id)}/deliveries`, {
          cursor: pages.at(-1),
          status,
          inbox_state: inbox,
        }),
    origin ? undefined : 10000,
  );
  const originQuery = useQuery({
    queryKey: app.queryKey("delivery-origin", claw.id, status, inbox, origin),
    enabled: !!origin,
    queryFn: async ({ signal }) => {
      let cursor: string | null = null;
      const matches: Delivery[] = [];
      do {
        const page: Collection<Delivery> = await json(
          await app.request(
            url(`${clawPath(claw.id)}/deliveries`, {
              cursor,
              limit: "100",
              status,
              inbox_state: inbox,
            }),
            { signal },
          ),
        );
        matches.push(
          ...page.items.filter(
            (row) =>
              row.origin === origin ||
              (origin === "input" &&
                ["workspace", "console"].includes(row.origin)),
          ),
        );
        cursor = page.next_cursor;
      } while (cursor);
      return matches;
    },
  });
  const all = { value: "all", label: copy("All", "全部") };
  return (
    <Page
      title={copy("Deliveries", "投递记录")}
      actions={picker}
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
                onValueChange={(value) => {
                  setLocalPage(0);
                  select({ admission: value === "all" ? null : value });
                }}
                options={[
                  all,
                  { value: "accepted", label: copy("Accepted", "已接收") },
                  { value: "full", label: copy("Inbox full", "收件箱已满") },
                ]}
              />
              <SegmentedControl
                label={copy("Inbox state", "收件状态")}
                size="sm"
                value={inbox || "all"}
                onValueChange={(value) => {
                  setLocalPage(0);
                  select({ inbox: value === "all" ? null : value });
                }}
                options={[
                  all,
                  { value: "unread", label: copy("Unread", "未读") },
                  { value: "read", label: copy("Read", "已读") },
                ]}
              />
              <ChoiceField
                label={copy("Origin", "来源")}
                hideLabel
                variant="filter"
                value={origin || "all"}
                onValueChange={(value) => {
                  setLocalPage(0);
                  select({ origin: value === "all" ? null : value });
                }}
                options={[
                  { ...all, label: copy("All origins", "所有来源") },
                  { value: "github", label: "GitHub" },
                  {
                    value: "lark",
                    label: copy("Lark / Feishu", "飞书 / Lark"),
                  },
                  { value: "worker", label: copy("Worker", "工作线程") },
                  { value: "schedule", label: copy("Schedule", "排程") },
                  {
                    value: "input",
                    label: copy("Headless input", "无界面输入"),
                  },
                ]}
              />
              {origin && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={originQuery.isFetching}
                  onClick={() => {
                    setLocalPage(0);
                    void originQuery.refetch();
                  }}
                >
                  <Icon name="refresh" />
                  {copy("Refresh results", "刷新结果")}
                </Button>
              )}
            </>
          }
        />
      }
    >
      {origin ? (
        originQuery.isPending ? (
          <Loading />
        ) : originQuery.isError ? (
          <ErrorPage error={originQuery.error} />
        ) : (
          <>
            <DeliveryTable
              rows={originQuery.data.slice(
                localPage * 30,
                (localPage + 1) * 30,
              )}
              claw={claw.id}
            />
            <CollectionFooter
              count={copy(
                `${originQuery.data.length} results`,
                `${originQuery.data.length} 条结果`,
              )}
            >
              <div className={styles.actions}>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={localPage === 0}
                  onClick={() => setLocalPage(localPage - 1)}
                >
                  {copy("Previous", "上一页")}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={(localPage + 1) * 30 >= originQuery.data.length}
                  onClick={() => setLocalPage(localPage + 1)}
                >
                  {copy("Next", "下一页")}
                </Button>
              </div>
            </CollectionFooter>
          </>
        )
      ) : query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPage error={query.error} />
      ) : (
        <>
          <DeliveryTable rows={query.data.items} claw={claw.id} />
          <CollectionFooter>
            <Pager
              nextCursor={query.data.next_cursor}
              pages={pages}
              setPages={(pages) => setPaging({ filter, pages })}
            />
          </CollectionFooter>
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
    origin: params.get("origin"),
  });
  const source = row.source ?? {};
  const sourceSession =
    typeof source.session_id === "string" ? source.session_id : null;
  const sourceThread =
    typeof source.thread_id === "string" ? source.thread_id : null;
  const sourceRun = typeof source.run_id === "string" ? source.run_id : null;
  const connector =
    typeof source.connector_id === "string" ? source.connector_id : null;
  const payload = JSON.stringify(source, null, 2);
  const rail = (
    <Rail>
      <RailSection title={copy("Delivery", "投递记录")}>
        <RailRow label={copy("Status", "状态")}>
          <Status state={row.status} />
        </RailRow>
        <RailRow label={copy("Origin", "来源")}>
          {sourceLabel(row.origin)}
        </RailRow>
        <RailRow label={copy("Kind", "类型")}>{row.kind ?? "—"}</RailRow>
        <RailRow label={copy("Received", "接收时间")}>
          <DateTime value={row.created_at} />
        </RailRow>
        <RailRow label={copy("Read", "已读时间")}>
          <DateTime value={row.read_at} />
        </RailRow>
        <RailRow label={copy("Inbox state", "收件状态")}>
          {state ? <Status state={state} /> : "—"}
        </RailRow>
      </RailSection>
      <RailSection title={copy("Execution", "执行")}>
        {row.thread_id && (
          <RailRow label={copy("Main Thread", "主线程")}>
            <ThreadLink thread={row.thread_id} session={row.session_id}>
              {copy("Open Thread", "打开线程")}
            </ThreadLink>
          </RailRow>
        )}
        {row.run_id && (
          <RailRow label={copy("Main Run", "主线程运行")}>
            <ThreadLink
              thread={row.thread_id}
              session={row.session_id}
              run={row.run_id}
            >
              {copy("Open Run", "打开运行")}
            </ThreadLink>
          </RailRow>
        )}
        {sourceThread && sourceSession && (
          <RailRow label={copy("Source Worker Thread", "来源工作线程")}>
            <ThreadLink thread={sourceThread} session={sourceSession}>
              {copy("Open Thread", "打开线程")}
            </ThreadLink>
          </RailRow>
        )}
        {sourceRun && sourceThread && sourceSession && (
          <RailRow label={copy("Source Worker Run", "来源工作线程运行")}>
            <ThreadLink
              thread={sourceThread}
              session={sourceSession}
              run={sourceRun}
            >
              {copy("Open Run", "打开运行")}
            </ThreadLink>
          </RailRow>
        )}
        {connector && (
          <RailRow label={copy("Source Connector", "来源连接器")}>
            <ClawLink id={claw.id} page="connectors" query={{ connector }}>
              {copy("Open connector", "打开连接器")}
            </ClawLink>
          </RailRow>
        )}
      </RailSection>
    </Rail>
  );
  return (
    <DetailPage
      back={back}
      backLabel={copy("Deliveries", "投递记录")}
      header={
        <DetailHeader
          avatar={<Icon name="inbox" />}
          name={sourceLabel(row.origin)}
          resourceKey={row.id}
          status={<Status state={row.status} />}
        />
      }
    >
      <DetailLayout rail={rail}>
        <div className={styles.detailSections}>
          <Section
            title={copy("Message", "消息")}
            actions={row.content !== null && <CopyButton value={row.content} />}
          >
            {row.content === null ? (
              <p className={styles.muted}>
                {row.item_id
                  ? copy("Message content has expired.", "消息内容已过期。")
                  : copy("No Inbox item was created.", "没有创建收件条目。")}
              </p>
            ) : (
              <pre className={styles.payload}>{row.content}</pre>
            )}
          </Section>
          {Object.keys(source).length > 0 && (
            <Section
              title={copy("Source payload", "来源数据")}
              actions={<CopyButton value={payload} />}
            >
              <pre className={`${styles.payload} ${styles.jsonPayload}`}>
                {payload}
              </pre>
            </Section>
          )}
        </div>
      </DetailLayout>
    </DetailPage>
  );
}
