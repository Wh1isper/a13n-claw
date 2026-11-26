import {
  Badge,
  Button,
  ChoiceField,
  SearchPicker,
  useInfiniteQuery,
  Pagination,
  Empty,
  ErrorPage,
  Input,
  Link,
  Loading,
  React,
  Timestamp,
  useAccess,
  useApp,
} from "@a13n/console";
import {
  appPath,
  json,
  clawPath,
  threadPath,
  url,
  useRead,
  useSelection,
  type Claw,
  type Collection,
  type Overview,
} from "./data";
import { useCopy } from "./copy";
import styles from "./app.module.css";
import { Icon } from "./ui/icons";

export function Status({ state }: { state: string }) {
  const { copy } = useCopy();
  const zh: Record<string, string> = {
    completed: "已完成",
    enabled: "已启用",
    accepted: "已接收",
    read: "已读",
    ready: "就绪",
    verified: "已验证",
    connected: "已连接",
    succeeded: "成功",
    active: "运行中",
    running: "运行中",
    waiting: "等待处理",
    unread: "未读",
    processing: "处理中",
    queued: "排队中",
    creating: "创建中",
    starting: "启动中",
    reserved: "已预留",
    "not observed": "尚未观测",
    late: "超时跳过",
    overlap: "重叠跳过",
    unknown: "未知",
    missing: "缺失",
    failed: "失败",
    full: "收件箱已满",
    cancelled: "已取消",
    disconnected: "已断开",
    unavailable: "不可用",
    lost: "已丢失",
    conflict: "冲突",
    expired: "已过期",
    deleted: "已删除",
    duplicate: "重复",
    idle: "空闲",
    paused: "已暂停",
    disabled: "已停用",
    stopped: "已停止",
    "not started": "未启动",
    "not configured": "未配置",
    saved: "已保存",
    authorized: "已授权",
    off: "关闭",
    "reauthorize needed": "需要重新授权",
    archived: "已归档",
    outdated: "有可用更新",
    updating: "更新中",
    rejected: "已拒绝",
    revoked: "已撤销",
  };
  const label = copy(
    state === "full" ? "Inbox full" : state.replaceAll("_", " "),
    zh[state] ?? state,
  );
  const variants = {
    success: [
      "completed",
      "enabled",
      "accepted",
      "read",
      "ready",
      "verified",
      "connected",
      "succeeded",
      "active",
      "running",
      "authorized",
      "saved",
    ],
    warning: [
      "waiting",
      "unread",
      "processing",
      "queued",
      "creating",
      "starting",
      "reserved",
      "not observed",
      "late",
      "overlap",
      "unknown",
      "reauthorize needed",
      "outdated",
      "updating",
      "paused",
      "stopped",
    ],
    error: [
      "failed",
      "missing",
      "rejected",
      "revoked",
      "full",
      "cancelled",
      "disconnected",
      "unavailable",
      "lost",
      "conflict",
    ],
    secondary: ["idle", "disabled", "not started", "not configured"],
  } as const;
  const variant =
    (Object.keys(variants) as (keyof typeof variants)[]).find((key) =>
      (variants[key] as readonly string[]).includes(state),
    ) ?? "secondary";
  return (
    <span
      className={styles.status}
      data-variant={variant}
      data-live={["active", "enabled", "running"].includes(state) || undefined}
    >
      <span className={styles.dot} />
      {label}
    </span>
  );
}
export function DateTime({ value }: { value: string | null }) {
  return value ? (
    <Timestamp value={value} />
  ) : (
    <span className={styles.muted}>—</span>
  );
}
export function ThreadLink({
  session,
  thread,
  run,
  children,
}: {
  session: string | null;
  thread: string | null;
  run?: string | null;
  children: React.ReactNode;
}) {
  const { workspaceId } = useApp();
  if (!session || !thread || !workspaceId) return null;
  return (
    <Link
      className={styles.link}
      to={threadPath(workspaceId, session, thread, run)}
    >
      {children}
      <Icon name="external" />
    </Link>
  );
}
export function ClawLink({
  id,
  page,
  children,
  query,
}: {
  id?: string;
  page: string;
  query?: Record<string, string | null | undefined>;
  children: React.ReactNode;
}) {
  const { workspaceId } = useApp();
  return (
    <Link
      className={styles.link}
      to={url(appPath(workspaceId!, page), { claw: id, ...query })}
    >
      {children}
      <Icon name="next" />
    </Link>
  );
}
export function ResourceLink({
  path,
  children,
}: {
  path: string;
  children: React.ReactNode;
}) {
  const { workspaceId } = useApp();
  return (
    <Link className={styles.link} to={`/workspace/${workspaceId}/${path}`}>
      {children}
      <Icon name="external" />
    </Link>
  );
}
export function ClawScope({
  children,
  setup,
}: {
  children: (claw: Claw, picker: React.ReactNode) => React.ReactNode;
  setup?: React.ReactNode;
}) {
  const { params, select } = useSelection();
  const { copy } = useCopy();
  const { can } = useAccess();
  const app = useApp();
  const claws = useInfiniteQuery({
    queryKey: app.queryKey("/claws", "switcher"),
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) =>
      json<Collection<Claw>>(
        await app.request(url("/claws", { limit: "50", cursor: pageParam }), {
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor,
  });
  const { workspaceId } = app;
  const rows = claws.data?.pages.flatMap((page) => page.items) ?? [];
  const storageKey = `a13n.claw.selection.${workspaceId ?? ""}`;
  const explicit = params.get("claw");
  const remembered = window.sessionStorage.getItem(storageKey);
  const wanted = explicit ?? remembered;

  const listed = rows.find(({ id }) => id === wanted);
  const chosen = useRead<Overview>(wanted && !listed ? clawPath(wanted) : null);
  const pick = (claw: string | null) => {
    if (claw) window.sessionStorage.setItem(storageKey, claw);
    else window.sessionStorage.removeItem(storageKey);
    select({ claw, delivery: null, connector: null, schedule: null });
  };
  const selected = listed ?? chosen.data?.claw;
  React.useEffect(() => {
    if (selected) window.sessionStorage.setItem(storageKey, selected.id);
    else if (!explicit && remembered && chosen.isError)
      window.sessionStorage.removeItem(storageKey);
  }, [selected, explicit, remembered, chosen.isError, storageKey]);
  if (claws.isPending || (wanted && !listed && chosen.isPending))
    return <Loading page />;
  if (claws.isError) return <ErrorPage error={claws.error} />;
  const claw = selected ?? (explicit ? undefined : rows[0]);
  if (!claw)
    return wanted ? (
      <Empty
        title={copy("Claw unavailable", "Claw 不可用")}
        description={copy(
          "Choose an active Claw in this workspace.",
          "请选择此工作区中未归档的 Claw。",
        )}
        action={
          <Button variant="outline" onClick={() => pick(null)}>
            {copy("Choose a Claw", "选择 Claw")}
          </Button>
        }
      />
    ) : (
      (setup ?? (
        <Empty
          title={copy("No Claws in this workspace", "此工作区还没有 Claw")}
          description={copy(
            "A Claw brings incoming work into one persistent Main Thread.",
            "Claw 将传入工作汇集到一个持久的主线程。",
          )}
          action={
            can("admin") && (
              <ClawLink page="settings">
                {copy("Set up Claw", "设置 Claw")}
              </ClawLink>
            )
          }
        />
      ))
    );
  const options = listed || !wanted ? rows : [claw, ...rows];
  return children(
    claw,
    options.length === 1 && !claws.hasNextPage ? null : (
      <SearchPicker
        label="Claw"
        placeholder={copy("Choose a Claw", "选择 Claw")}
        emptyMessage={copy("No matching Claws", "没有匹配的 Claw")}
        value={claw.id}
        onValueChange={pick}
        groups={[
          {
            label: "Claw",
            options: options.map((item) => ({
              value: item.id,
              label: item.name,
            })),
          },
        ]}
        footer={
          claws.hasNextPage && (
            <Button
              type="button"
              variant="ghost"
              disabled={claws.isFetchingNextPage}
              onClick={() => void claws.fetchNextPage()}
            >
              {copy("Load more", "加载更多")}
            </Button>
          )
        }
      />
    ),
  );
}
export function Pager({
  nextCursor,
  pages,
  setPages,
}: {
  nextCursor?: string | null;
  pages: string[];
  setPages: (pages: string[]) => void;
}) {
  return (
    <Pagination
      next={nextCursor}
      page={{
        cursor: pages.at(-1),
        next: (cursor) => setPages([...pages, cursor]),
        previous: pages.length ? () => setPages(pages.slice(0, -1)) : undefined,
      }}
    />
  );
}

export function InputValue({
  name,
  value,
  maxLength,
  required = true,
}: {
  name: string;
  value?: string | number | null;
  maxLength?: number;
  required?: boolean;
}) {
  return (
    <Input
      name={name}
      defaultValue={value ?? ""}
      maxLength={maxLength}
      required={required}
    />
  );
}
