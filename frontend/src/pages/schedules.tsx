import {
  Button,
  Checkbox,
  ChoiceField,
  Confirm,
  DetailHeader,
  DetailLayout,
  DetailPage,
  Empty,
  ErrorNotice,
  ErrorPage,
  FormActions,
  FormField,
  FormSection,
  Input,
  Loading,
  Page,
  Rail,
  RailRow,
  RailSection,
  React,
  ResourceTable,
  SaveBar,
  SearchPicker,
  Section,
  Sections,
  SegmentedControl,
  Select,
  SelectItem,
  SelectPopup,
  SelectTrigger,
  SelectValue,
  Switch,
  Textarea,
  useAccess,
  useApp,
  useQuery,
  useToast,
} from "@a13n/console";
import {
  appPath,
  json,
  clawPath,
  url,
  useRead,
  useProjectResources,
  useSelection,
  useWrite,
  type Claw,
  type Collection,
  type Connector,
  type Occurrence,
  type Overview,
  type ResourceKind,
  type Schedule,
} from "../data";
import {
  ClawLink,
  ClawScope,
  DateTime,
  InputValue,
  Pager,
  Status,
  ThreadLink,
} from "../primitives";
import { useCopy } from "../copy";
import styles from "../app.module.css";
import { Icon } from "../ui/icons";
import { CopyButton } from "../ui/copy-button";

import { field, items, list, ResourcePicker } from "./form-common";

export function SchedulesPage() {
  const { params } = useSelection();
  const id = params.get("schedule");
  return (
    <ClawScope>
      {(claw, picker) =>
        id ? (
          <ScheduleRoute key={id} claw={claw} id={id} />
        ) : (
          <ScheduleList key={claw.id} claw={claw} picker={picker} />
        )
      }
    </ClawScope>
  );
}
function ScheduleList({
  claw,
  picker,
}: {
  claw: Claw;
  picker: React.ReactNode;
}) {
  const { copy } = useCopy();
  const { can } = useAccess();
  const { params, select } = useSelection();
  const app = useApp();
  const [stateFilter, setStateFilter] = React.useState("all");
  const [localPage, setLocalPage] = React.useState(0);
  const modeFilter = params.get("mode") ?? "all";
  const [pages, setPages] = React.useState<string[]>([]);
  const query = useRead<Collection<Schedule>>(
    stateFilter !== "all"
      ? null
      : url(`${clawPath(claw.id)}/schedules`, {
          target: modeFilter === "all" ? null : modeFilter,
          cursor: pages.at(-1),
        }),
    stateFilter === "all" ? 10000 : undefined,
  );
  const filtered = useQuery({
    queryKey: app.queryKey("schedule-state", claw.id, modeFilter, stateFilter),
    enabled: stateFilter !== "all",
    queryFn: async ({ signal }) => {
      let cursor: string | null = null;
      const rows: Schedule[] = [];
      do {
        const page: Collection<Schedule> = await json(
          await app.request(
            url(`${clawPath(claw.id)}/schedules`, {
              cursor,
              limit: "100",
              target: modeFilter === "all" ? null : modeFilter,
            }),
            { signal },
          ),
        );
        rows.push(
          ...page.items.filter(
            (row) => row.enabled === (stateFilter === "active"),
          ),
        );
        cursor = page.next_cursor;
      } while (cursor);
      return rows;
    },
  });
  const listQuery = stateFilter === "all" ? query : filtered;
  const rows =
    stateFilter === "all"
      ? query.data?.items
      : filtered.data?.slice(localPage * 30, (localPage + 1) * 30);
  const add = can("write") && (
    <Button onClick={() => select({ schedule: "new" })}>
      {copy("New schedule", "新建排程")}
    </Button>
  );
  return (
    <Page
      title={copy("Schedules", "排程")}
      actions={
        <div className={`${styles.actions} ${styles.headerActions}`}>
          {picker}
          {!!rows?.length && add}
        </div>
      }
      description={copy(
        "Schedule work for Main or start a new native Thread on each occurrence.",
        "将工作安排给主线程，或在每次触发时启动一个新的原生线程。",
      )}
      toolbar={
        <div className={styles.actions}>
          <SegmentedControl
            label={copy("Schedule mode", "排程模式")}
            value={modeFilter}
            onValueChange={(value) => {
              setPages([]);
              setLocalPage(0);
              select({ mode: value === "all" ? null : value });
            }}
            options={[
              { value: "all", label: copy("All", "全部") },
              { value: "main", label: copy("Main", "主线程") },
              { value: "fresh", label: copy("Fresh Threads", "新线程") },
            ]}
          />
          <SegmentedControl
            label={copy("Schedule status", "排程状态")}
            size="sm"
            value={stateFilter}
            onValueChange={(value) => {
              setStateFilter(value);
              setLocalPage(0);
            }}
            options={[
              { value: "all", label: copy("All", "全部") },
              { value: "active", label: copy("Active", "启用") },
              { value: "paused", label: copy("Paused", "已暂停") },
            ]}
          />
        </div>
      }
    >
      {listQuery.isPending ? (
        <Loading />
      ) : listQuery.isError ? (
        <ErrorPage error={listQuery.error} />
      ) : !rows?.length ? (
        <Empty
          title={copy("No schedules in this mode", "此模式下没有排程")}
          description={copy(
            "Schedules appear here after creation.",
            "创建排程后将在这里显示。 ",
          )}
          action={add}
        />
      ) : (
        <>
          <ResourceTable
            caption={copy("Schedules", "排程")}
            items={rows}
            onRowActivate={(row) => select({ schedule: row.id })}
            columns={[
              {
                label: copy("Name", "名称"),
                tone: "primary",
                render: (row) => (
                  <span>
                    <ClawLink
                      id={claw.id}
                      page="schedules"
                      query={{ schedule: row.id }}
                    >
                      {row.name}
                    </ClawLink>
                    <span className={styles.subtitle}>
                      {row.cron ?? copy("One time", "单次")}
                    </span>
                  </span>
                ),
              },
              {
                label: copy("Target", "目标"),
                render: (row) =>
                  row.target === "fresh"
                    ? copy("Fresh Thread", "新线程")
                    : copy("Main", "主线程"),
              },
              {
                label: copy("State", "状态"),
                render: (row) => (
                  <Status state={row.enabled ? "enabled" : "paused"} />
                ),
              },
              {
                label: copy("Next occurrence", "下次触发"),
                render: (row) => <DateTime value={row.next_at} />,
              },
            ]}
          />
          {stateFilter === "all" ? (
            <Pager
              nextCursor={query.data?.next_cursor}
              pages={pages}
              setPages={setPages}
            />
          ) : (
            <div className={styles.actions}>
              <Button
                variant="ghost"
                disabled={localPage === 0}
                onClick={() => setLocalPage(localPage - 1)}
              >
                {copy("Previous", "上一页")}
              </Button>
              <Button
                variant="ghost"
                disabled={(localPage + 1) * 30 >= (filtered.data?.length ?? 0)}
                onClick={() => setLocalPage(localPage + 1)}
              >
                {copy("Next", "下一页")}
              </Button>
            </div>
          )}
        </>
      )}
    </Page>
  );
}

function ScheduleRoute({ claw, id }: { claw: Claw; id: string }) {
  const { params, select } = useSelection();
  const { can } = useAccess();
  const { copy } = useCopy();
  const fresh = params.get("mode") === "fresh";
  const edit = params.get("edit") === "1";
  const query = useRead<Schedule>(
    id === "new"
      ? null
      : `${clawPath(claw.id)}/schedules/${encodeURIComponent(id)}`,
    edit ? undefined : 10000,
  );
  const close = () =>
    select(id === "new" ? { schedule: null, edit: null } : { edit: null });
  if (id === "new")
    return can("write") ? (
      <ScheduleEditor claw={claw} fresh={fresh} close={close} />
    ) : (
      <ErrorPage
        error={
          new Error(
            copy("Schedule writing is not permitted.", "没有写入排程的权限。"),
          )
        }
      />
    );
  if (query.isPending) return <Loading page />;
  if (query.isError) return <ErrorPage error={query.error} />;
  if (edit && can("write") && !query.data.deleted_at)
    return (
      <ScheduleEditor
        claw={claw}
        row={query.data}
        fresh={query.data.target === "fresh"}
        close={close}
      />
    );
  return <ScheduleDetails claw={claw} row={query.data} />;
}

export function requestForPayload(
  previous: { payload: string; key: string } | null,
  payload: string,
) {
  return previous?.payload === payload
    ? previous
    : { payload, key: crypto.randomUUID() };
}

function localTime(value: string | null | undefined) {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 19);
}

function ScheduleEditor({
  claw,
  row,
  fresh,
  close,
}: {
  claw: Claw;
  row?: Schedule;
  fresh: boolean;
  close: () => void;
}) {
  const { copy } = useCopy();
  const { workspaceId } = useApp();
  const [mode, setMode] = React.useState(row && !row.cron ? "once" : "cron");
  const [target, setTarget] = React.useState(
    row?.target ?? (fresh && claw.environment_id ? "fresh" : "main"),
  );
  const [changeRule, setChangeRule] = React.useState(!row);
  const write = useWrite<Schedule>();
  const request = React.useRef<{ payload: string; key: string } | null>(null);
  const back = url(appPath(workspaceId!, "schedules"), {
    claw: claw.id,
    mode: fresh ? "fresh" : null,
    schedule: row?.id,
  });
  return (
    <Page
      title={
        row
          ? copy("Edit schedule", "编辑排程")
          : copy("New schedule", "新建排程")
      }
      back={back}
      backLabel={copy("Schedules", "排程")}
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          if (target === "fresh" && !claw.environment_id) return;
          const form = new FormData(event.currentTarget);
          const body = {
            name: field(form, "name"),
            prompt: field(form, "prompt"),
            target,
            lateness_seconds: Number(field(form, "lateness")),
            ...(changeRule
              ? {
                  rule:
                    mode === "cron"
                      ? {
                          type: "cron",
                          expression: field(form, "cron"),
                          timezone: field(form, "zone"),
                        }
                      : {
                          type: "once",
                          run_at: new Date(field(form, "time")).toISOString(),
                        },
                }
              : {}),
          };
          const path = `${clawPath(claw.id)}/schedules${row ? `/${row.id}` : ""}`;
          request.current = requestForPayload(
            request.current,
            JSON.stringify([path, body]),
          );
          write.mutate(
            {
              path,
              method: row ? "PUT" : "POST",
              body,
              key: request.current.key,
            },
            { onSuccess: close },
          );
        }}
      >
        <FormSection title={copy("Task", "任务")}>
          <ChoiceField
            label={copy("Target", "目标")}
            value={target}
            onValueChange={(value) =>
              setTarget(value === "fresh" ? "fresh" : "main")
            }
            options={[
              { value: "main", label: copy("Main", "主线程") },
              {
                value: "fresh",
                label: copy("Fresh Thread", "新线程"),
                disabled: !claw.environment_id,
              },
            ]}
            description={
              !claw.environment_id
                ? copy(
                    "Bind a Project Environment to use a Fresh Thread.",
                    "绑定项目环境后可使用新线程。",
                  )
                : undefined
            }
          />
          <FormField label={copy("Name", "名称")}>
            <InputValue name="name" value={row?.name} maxLength={128} />
          </FormField>
          <FormField label={copy("Prompt", "提示词")}>
            <Textarea
              name="prompt"
              defaultValue={row?.prompt}
              rows={5}
              required
              maxLength={32000}
            />
          </FormField>
        </FormSection>
        <FormSection title={copy("Timing", "时间设置")}>
          {row && (
            <FormField
              data-field-layout="setting"
              label={copy("Change the schedule rule", "更改排程规则")}
            >
              <Switch checked={changeRule} onCheckedChange={setChangeRule} />
            </FormField>
          )}
          {changeRule && (
            <>
              <ChoiceField
                label={copy("Frequency", "频率")}
                value={mode}
                onValueChange={setMode}
                options={[
                  { value: "cron", label: copy("Recurring", "重复执行") },
                  { value: "once", label: copy("Once", "仅一次") },
                ]}
              />
              {mode === "cron" ? (
                <>
                  <FormField label={copy("Cron expression", "Cron 表达式")}>
                    <InputValue name="cron" value={row?.cron} maxLength={128} />
                  </FormField>
                  <FormField label={copy("Time zone", "时区")}>
                    <InputValue
                      name="zone"
                      value={row?.timezone ?? "UTC"}
                      maxLength={100}
                    />
                  </FormField>
                </>
              ) : (
                <FormField
                  label={copy("Run at (local time)", "运行时间（本地时间）")}
                >
                  <Input
                    type="datetime-local"
                    name="time"
                    step={1}
                    required
                    defaultValue={localTime(row?.cron ? null : row?.next_at)}
                  />
                </FormField>
              )}
            </>
          )}
          <FormField
            data-field-layout="setting"
            label={copy("Lateness allowance (seconds)", "允许迟到时间（秒）")}
            description={copy(
              "Occurrences later than this are skipped.",
              "超过此时间的触发会被跳过。",
            )}
          >
            <Input
              name="lateness"
              type="number"
              min={60}
              max={86400}
              required
              defaultValue={row?.lateness_seconds ?? 300}
            />
          </FormField>
        </FormSection>
        {write.isError && <ErrorNotice error={write.error} />}
        <FormActions
          onCancel={close}
          label={copy("Save schedule", "保存排程")}
          pending={write.isPending}
        />
      </form>
    </Page>
  );
}

function ScheduleDetails({ claw, row }: { claw: Claw; row: Schedule }) {
  const { copy } = useCopy();
  const { can } = useAccess();
  const { workspaceId } = useApp();
  const { select } = useSelection();
  const write = useWrite<Schedule>();
  const triggerKey = React.useRef<string | null>(null);
  const [pages, setPages] = React.useState<string[]>([]);
  const path = `${clawPath(claw.id)}/schedules/${row.id}`;
  const query = useRead<Collection<Occurrence>>(
    url(`${path}/occurrences`, { cursor: pages.at(-1) }),
    10000,
  );
  const back = url(appPath(workspaceId!, "schedules"), { claw: claw.id });
  return (
    <DetailPage
      back={back}
      backLabel={copy("Schedules", "排程")}
      header={
        <DetailHeader
          avatar={<Icon name="clock" />}
          name={row.name}
          resourceKey={row.id}
          status={
            <Status
              state={
                row.deleted_at ? "deleted" : row.enabled ? "enabled" : "paused"
              }
            />
          }
          actions={
            can("write") &&
            !row.deleted_at && (
              <div className={styles.actions}>
                <Button
                  variant="outline"
                  disabled={write.isPending}
                  onClick={() => {
                    triggerKey.current ??= crypto.randomUUID();
                    write.mutate(
                      {
                        path: `${path}/trigger`,
                        method: "POST",
                        body: {},
                        key: triggerKey.current,
                      },
                      {
                        onSuccess: () => {
                          triggerKey.current = null;
                        },
                      },
                    );
                  }}
                >
                  {copy("Trigger now", "立即触发")}
                </Button>
                <Button variant="outline" onClick={() => select({ edit: "1" })}>
                  {copy("Edit", "编辑")}
                </Button>
                <Button
                  variant="outline"
                  disabled={
                    write.isPending ||
                    (!row.enabled && !row.cron && !row.next_at)
                  }
                  onClick={() =>
                    write.mutate({
                      path,
                      method: "PATCH",
                      body: { enabled: !row.enabled },
                    })
                  }
                >
                  {row.enabled ? copy("Pause", "暂停") : copy("Resume", "恢复")}
                </Button>
                <Confirm
                  title={copy("Delete schedule?", "删除排程？")}
                  subject={row.name}
                  danger
                  trigger={copy("Delete", "删除")}
                  description={copy(
                    "Future occurrences stop. Existing occurrences and Inbox deliveries remain.",
                    "停止将来触发，保留已有触发记录和收件投递。",
                  )}
                  action={() =>
                    write.mutateAsync({ path, method: "DELETE", body: null })
                  }
                />
              </div>
            )
          }
        />
      }
    >
      <DetailLayout
        rail={
          <Rail>
            <RailSection title={copy("Timing", "时间设置")}>
              <RailRow label={copy("Target", "目标")}>
                {row.target === "fresh"
                  ? copy("Fresh Thread", "新线程")
                  : copy("Main", "主线程")}
              </RailRow>
              <RailRow label={copy("Next occurrence", "下次触发")}>
                <DateTime value={row.next_at} />
              </RailRow>
              {row.cron && (
                <RailRow label={copy("Cron", "Cron")}>
                  <code>{row.cron}</code>
                </RailRow>
              )}
              <RailRow label={copy("Time zone", "时区")}>
                {row.timezone}
              </RailRow>
              <RailRow label={copy("Lateness (seconds)", "允许迟到（秒）")}>
                {row.lateness_seconds}
              </RailRow>
            </RailSection>
          </Rail>
        }
      >
        {write.isError && <ErrorNotice error={write.error} />}
        <div className={styles.detailSections}>
          <Section
            title={copy("Prompt", "提示词")}
            actions={<CopyButton value={row.prompt} />}
          >
            <pre className={styles.payload}>{row.prompt}</pre>
          </Section>
          <Section title={copy("Occurrences", "触发记录")}>
            {query.isPending ? (
              <Loading />
            ) : query.isError ? (
              <ErrorNotice error={query.error} />
            ) : query.data.items.length === 0 ? (
              <Empty
                title={copy("No occurrences yet", "暂时没有触发记录")}
                description={copy(
                  "The next due occurrence will appear here.",
                  "下一次到期触发后将在这里显示。 ",
                )}
              />
            ) : (
              <>
                <ResourceTable
                  caption={copy("Occurrences", "触发记录")}
                  items={query.data.items}
                  columns={[
                    {
                      label: copy("Scheduled at", "计划时间"),
                      render: (item) => <DateTime value={item.scheduled_at} />,
                    },
                    {
                      label: copy("Outcome", "结果"),
                      render: (item) => <Status state={item.status} />,
                    },
                    {
                      label: copy("Delivery", "投递"),
                      render: (item) =>
                        item.delivery_id ? (
                          <ClawLink
                            id={claw.id}
                            page="deliveries"
                            query={{ delivery: item.delivery_id }}
                          >
                            {copy("Open delivery", "打开投递")}
                          </ClawLink>
                        ) : (
                          "—"
                        ),
                    },
                    {
                      label: copy("Execution", "执行"),
                      render: (item) =>
                        item.run_id ? (
                          <ThreadLink
                            thread={item.thread_id ?? claw.main_thread_id}
                            session={item.session_id ?? claw.main_session_id}
                            run={item.run_id}
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
                  nextCursor={query.data.next_cursor}
                  pages={pages}
                  setPages={setPages}
                />
              </>
            )}
          </Section>
        </div>
      </DetailLayout>
    </DetailPage>
  );
}
