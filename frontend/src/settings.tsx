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
  useToast,
} from "@a13n/console";
import {
  appPath,
  clawPath,
  url,
  useRead,
  useProjectResources,
  useSelection,
  useWrite,
  type Claw,
  type Collection,
  type Connector,
  type FreshSchedule,
  type Occurrence,
  type Overview,
  type ResourceKind,
  type Schedule,
} from "./data";
import {
  ClawLink,
  ClawScope,
  DateTime,
  InputValue,
  Pager,
  ResourceLink,
  Status,
  ThreadLink,
} from "./primitives";
import { useCopy } from "./copy";
import styles from "./app.module.css";

const field = (form: FormData, name: string) =>
  String(form.get(name) ?? "").trim();
const items = (form: FormData, name: string) =>
  field(form, name)
    .split(/[\s,]+/)
    .filter(Boolean);
const list = (config: Connector["config"] | undefined, key: string) =>
  Array.isArray(config?.[key]) ? config[key].join("\n") : "";

const resourceCreation: Record<ResourceKind, string> = {
  agent: "agents/new",
  account: "settings/service-accounts",
  environment: "environments/instances",
  template: "environments/new",
  browser: "browsers",
  model: "models",
};

function ResourcePicker({
  label,
  kind,
  account,
  value,
  onChange,
  optional,
  disabled,
}: {
  label: string;
  kind: ResourceKind;
  account?: string;
  value: string;
  onChange: (value: string) => void;
  optional?: boolean;
  disabled?: boolean;
}) {
  const { copy } = useCopy();
  const choices = useProjectResources(kind, !disabled, account);
  const rows = choices.data?.pages.flatMap((page) => page.items) ?? [];
  const selected =
    value && !rows.some((row) => row.id === value)
      ? [{ value, label: value }]
      : [];
  const options = [
    ...(optional ? [{ value: "__none__", label: copy("None", "不绑定") }] : []),
    ...selected,
    ...rows.map(({ id, name }) => ({ value: id, label: name })),
  ];
  const id = React.useId();
  return (
    <div className={styles.picker}>
      <label htmlFor={id}>{label}</label>
      <SearchPicker
        id={id}
        label={label}
        value={value || (optional ? "__none__" : undefined)}
        disabled={disabled}
        placeholder={
          disabled
            ? copy("Select a service account first", "请先选择服务账号")
            : choices.isPending
              ? copy("Loading…", "加载中…")
              : copy("Select a resource", "选择资源")
        }
        emptyMessage={copy("No matching resources", "没有匹配的资源")}
        groups={[{ label, options }]}
        onValueChange={(value) => onChange(value === "__none__" ? "" : value)}
        footer={
          choices.hasNextPage && (
            <Button
              type="button"
              variant="ghost"
              disabled={choices.isFetchingNextPage}
              onClick={() => void choices.fetchNextPage()}
            >
              {copy("Load more", "加载更多")}
            </Button>
          )
        }
      />
      {!disabled &&
        (choices.isError || (!choices.isPending && !rows.length)) && (
          <div className={styles.actions}>
            {!choices.isError && (
              <ResourceLink path={resourceCreation[kind]}>
                {copy("Create in Console", "前往控制台创建")}
              </ResourceLink>
            )}
            <Button
              type="button"
              variant="ghost"
              onClick={() => void choices.refetch()}
            >
              {copy("Refresh choices", "刷新选项")}
            </Button>
          </div>
        )}
    </div>
  );
}

export function ConfigurationPage() {
  const { copy } = useCopy();
  const { params, select } = useSelection();
  const done = (claw: Claw) => select({ new: null, claw: claw.id });
  if (params.has("new"))
    return <ClawEditor done={done} cancel={() => select({ new: null })} />;
  return (
    <ClawScope setup={<ClawEditor done={done} />}>
      {(claw, picker) => (
        <Sections key={claw.id}>
          <Section
            title={claw.name}
            actions={
              <div className={styles.actions}>
                {picker}
                <Button variant="outline" onClick={() => select({ new: "1" })}>
                  {copy("New Claw", "新建 Claw")}
                </Button>
              </div>
            }
          >
            <ClawEditor claw={claw} />
          </Section>
          <ProjectResources
            claw={claw}
            archived={() => select({ claw: null })}
          />
        </Sections>
      )}
    </ClawScope>
  );
}

function ClawEditor({
  claw,
  done,
  cancel,
}: {
  claw?: Claw;
  done?: (claw: Claw) => void;
  cancel?: () => void;
}) {
  const { copy } = useCopy();
  const toast = useToast();
  const save = useWrite<Claw>();
  const [agent, setAgent] = React.useState("");
  const [account, setAccount] = React.useState("");
  const [environment, setEnvironment] = React.useState("");
  const [source, setSource] = React.useState("environment");
  const [browser, setBrowser] = React.useState("");
  const [models, setModels] = React.useState(
    (claw?.worker_models ?? []).map((model, key) => ({ ...model, key })),
  );
  const sequence = React.useRef(models.length);
  const [dirty, setDirty] = React.useState(false);
  const form = React.useRef<HTMLFormElement>(null);
  const discard = () => {
    form.current?.reset();
    setModels(
      (claw?.worker_models ?? []).map((model, key) => ({ ...model, key })),
    );
    setDirty(false);
    save.reset();
  };
  return (
    <form
      ref={form}
      className={styles.form}
      onChange={() => setDirty(true)}
      onSubmit={(event) => {
        event.preventDefault();
        const draft = new FormData(event.currentTarget);
        const body = {
          name: field(draft, "name"),
          heartbeat_seconds: Number(field(draft, "heartbeat")),
          unread_limit: Number(field(draft, "unread")),
          read_limit: Number(field(draft, "read")),
          worker_models: models.map(({ model, description }) => ({
            model,
            description: description.trim(),
          })),
          ...(!claw
            ? {
                agent_id: agent,
                service_account_id: account,
                browser_id: browser || null,
                ...(environment
                  ? {
                      [source === "template"
                        ? "environment_template_id"
                        : "environment_id"]: environment,
                    }
                  : {}),
              }
            : {}),
        };
        save.mutate(
          {
            path: claw ? `${clawPath(claw.id)}/settings` : "/claws",
            method: claw ? "PUT" : "POST",
            body,
          },
          {
            onSuccess: (result) => {
              setDirty(false);
              toast.add({
                title: copy("Claw settings saved", "Claw 设置已保存"),
              });
              done?.(result);
            },
          },
        );
      }}
    >
      <FormSection
        title={
          claw
            ? copy("Identity", "基本信息")
            : copy("Create a Claw", "创建 Claw")
        }
      >
        <FormField label={copy("Name", "名称")}>
          <InputValue name="name" value={claw?.name} maxLength={200} />
        </FormField>
        {!claw && (
          <>
            <ResourcePicker
              label={copy("Agent", "智能体")}
              kind="agent"
              value={agent}
              onChange={setAgent}
            />
            <ResourcePicker
              label={copy("Automation service account", "自动化服务账号")}
              kind="account"
              value={account}
              onChange={(value) => {
                setAccount(value);
                setEnvironment("");
                setBrowser("");
              }}
            />
          </>
        )}
      </FormSection>
      {!claw && (
        <FormSection
          title={copy("Project resources", "项目资源")}
          description={copy(
            "Bind resources now or configure them later.",
            "现在绑定资源，或在创建后配置。 ",
          )}
        >
          <ChoiceField
            label={copy("Environment source", "环境来源")}
            value={source}
            onValueChange={(value) => {
              setSource(value);
              setEnvironment("");
            }}
            options={[
              {
                value: "environment",
                label: copy("Existing instance", "现有实例"),
              },
              {
                value: "template",
                label: copy("Create from a template", "使用模板创建"),
              },
            ]}
          />
          <ResourcePicker
            label={
              source === "template"
                ? copy("Template (optional)", "模板（可选）")
                : copy("Environment (optional)", "环境（可选）")
            }
            kind={source === "template" ? "template" : "environment"}
            account={account}
            value={environment}
            onChange={setEnvironment}
            optional
            disabled={!account}
          />
          <ResourcePicker
            label={copy("Browser (optional)", "浏览器（可选）")}
            kind="browser"
            account={account}
            value={browser}
            onChange={setBrowser}
            optional
            disabled={!account}
          />
        </FormSection>
      )}
      <FormSection title={copy("Inbox and heartbeat", "收件箱与心跳")}>
        <FormField
          label={copy("Heartbeat interval (seconds)", "心跳间隔（秒）")}
          description={copy(
            "Set to 0 to disable idle follow-up.",
            "设为 0 时不触发空闲跟进。",
          )}
        >
          <Input
            name="heartbeat"
            type="number"
            required
            min={0}
            max={86400}
            defaultValue={claw?.heartbeat_seconds ?? 300}
          />
        </FormField>
        <FormField label={copy("Unread limit", "未读上限")}>
          <Input
            name="unread"
            type="number"
            required
            min={1}
            max={10000}
            defaultValue={claw?.unread_limit ?? 500}
          />
        </FormField>
        <FormField label={copy("Read history limit", "已读保留上限")}>
          <Input
            name="read"
            type="number"
            required
            min={1}
            max={10000}
            defaultValue={claw?.read_limit ?? 3000}
          />
        </FormField>
      </FormSection>
      <FormSection
        title={copy("Worker model guidance", "工作线程模型指引")}
        description={copy(
          "Describe when Main should select an alternative model for a worker.",
          "说明主线程何时应为工作线程选择其他模型。",
        )}
      >
        {models.map((row) => (
          <div className={styles.stack} key={row.key}>
            <ResourcePicker
              label={copy("Model", "模型")}
              kind="model"
              value={row.model}
              onChange={(value) => {
                setModels(
                  models.map((item) =>
                    item.key === row.key ? { ...item, model: value } : item,
                  ),
                );
                setDirty(true);
              }}
            />
            <FormField label={copy("When to use this model", "适用场景")}>
              <Textarea
                rows={2}
                required
                maxLength={500}
                value={row.description}
                onChange={(event) =>
                  setModels(
                    models.map((item) =>
                      item.key === row.key
                        ? { ...item, description: event.target.value }
                        : item,
                    ),
                  )
                }
              />
            </FormField>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setModels(models.filter((item) => item.key !== row.key));
                setDirty(true);
              }}
            >
              {copy("Remove guidance", "移除指引")}
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          disabled={models.length >= 20}
          onClick={() => {
            setModels([
              ...models,
              { key: sequence.current++, model: "", description: "" },
            ]);
            setDirty(true);
          }}
        >
          {copy("Add model guidance", "添加模型指引")}
        </Button>
      </FormSection>
      {save.isError && <ErrorNotice error={save.error} />}
      {claw ? (
        dirty && (
          <SaveBar
            title={copy("Unsaved changes", "有未保存的更改")}
            saveLabel={copy("Save settings", "保存设置")}
            discardLabel={copy("Discard", "放弃更改")}
            pending={save.isPending}
            disabled={models.some((m) => !m.model)}
            onDiscard={discard}
          />
        )
      ) : (
        <FormActions
          pending={save.isPending}
          disabled={!agent || !account || models.some((m) => !m.model)}
          onCancel={cancel}
          label={copy("Create Claw", "创建 Claw")}
        />
      )}
    </form>
  );
}

export function projectResourceState(state: string | null, permanent = false) {
  if (
    permanent ||
    (state &&
      ["missing", "lost", "failed", "deleted", "unavailable"].includes(state))
  )
    return "replace";
  return !state || state === "not configured"
    ? "bind"
    : state === "stopped"
      ? "resume"
      : "bound";
}

function ProjectResources({
  claw,
  archived,
}: {
  claw: Claw;
  archived: () => void;
}) {
  const { copy } = useCopy();
  const toast = useToast();
  const path = clawPath(claw.id);
  const overview = useRead<Overview>(path, 10000);
  const write = useWrite<Claw>();
  const [edit, setEdit] = React.useState<"environment" | "browser" | null>(
    null,
  );
  const [source, setSource] = React.useState("environment");
  const [selection, setSelection] = React.useState("");
  const [environmentChanged, setEnvironmentChanged] = React.useState(false);
  const snapshot = overview.data;
  if (!snapshot)
    return overview.isError ? (
      <ErrorNotice error={overview.error} />
    ) : (
      <Loading />
    );
  const project = snapshot.project;
  const linked = edit === "environment" ? claw.environment_id : claw.browser_id;
  const fresh = project.fresh_schedule_count > 0;
  const freshLink = (
    <ClawLink id={claw.id} page="schedules" query={{ mode: "fresh" }}>
      {copy("Open Fresh Schedules", "打开新线程排程")}
    </ClawLink>
  );
  const rebind = () =>
    write.mutateAsync({
      path: `${path}/project`,
      method: "PATCH",
      body: {
        [edit === "environment"
          ? source === "template"
            ? "environment_template_id"
            : "environment_id"
          : "browser_id"]: selection || null,
      },
    });
  const changed = () => {
    if (edit === "environment") {
      setEnvironmentChanged(true);
      toast.add({
        title: copy("Environment changed", "环境已更换"),
        description: copy(
          "Reauthorize connector replies in the new Environment.",
          "请为新环境重新授权连接器回复。",
        ),
      });
    } else
      toast.add({ title: copy("Browser binding saved", "浏览器绑定已保存") });
    setEdit(null);
  };
  return (
    <>
      <FormSection
        title={copy("Project", "项目")}
        description={copy(
          "Native resources remain owned by Service.",
          "原生资源由 Service 管理。",
        )}
      >
        <div className={styles.references}>
          <ResourceLink path={`agents/${claw.agent_id}`}>
            {copy("Open Agent", "打开智能体")}
          </ResourceLink>
          <ResourceLink path={`memory-spaces/${claw.memory_space_id}`}>
            {copy("Open Memory Space", "打开记忆空间")}
          </ResourceLink>
          <ResourceLink path="settings/service-accounts">
            {copy("Service accounts", "服务账号")}
          </ResourceLink>
        </div>
        {(["environment", "browser"] as const).map((kind) => {
          const env = kind === "environment";
          const name = env ? project.environment_name : project.browser_name;
          const state =
            (env ? project.environment : project.browser) ?? "not configured";
          const failure = env
            ? project.environment_failure?.message
            : project.browser_failure;
          const guidance = projectResourceState(
            state,
            env && project.environment_failure?.permanent,
          );
          const title = env
            ? copy("Environment", "环境")
            : copy("Browser", "浏览器");
          return (
            <div className={styles.stack} key={kind}>
              <div className={styles.actions}>
                <strong>
                  {title}
                  {name ? ` · ${name}` : ""}
                </strong>
                <Status state={state} />
                <ResourceLink
                  path={env ? "environments/instances" : "browsers"}
                >
                  {env
                    ? copy("Open Environments", "打开环境")
                    : copy("Open Browsers", "打开浏览器")}
                </ResourceLink>
                {guidance !== "resume" && (
                  <Button
                    variant="outline"
                    onClick={() => {
                      setEdit(kind);
                      setSource("environment");
                      setSelection(env ? "" : (claw.browser_id ?? ""));
                      write.reset();
                    }}
                  >
                    {guidance === "bind"
                      ? copy("Bind", "绑定")
                      : guidance === "replace"
                        ? copy("Replace", "更换")
                        : copy("Change binding", "更改绑定")}
                  </Button>
                )}
              </div>
              {guidance === "resume" && (
                <p className={styles.muted}>
                  {copy(
                    "Normally stopped. Start the same instance in Console; do not replace it.",
                    "此实例正常停止。请在控制台启动同一实例，无需更换。",
                  )}
                </p>
              )}
              {guidance === "replace" && (
                <p className={styles.error}>
                  {failure ||
                    copy(
                      "This resource is unavailable. Bind a replacement.",
                      "资源不可用，请绑定替代资源。",
                    )}
                </p>
              )}
              {guidance === "bind" && (
                <p className={styles.muted}>
                  {env
                    ? copy(
                        "Choose an existing instance or create from a template.",
                        "选择现有实例，或使用模板创建。",
                      )
                    : copy(
                        "You can bind an existing Browser or create one in Console.",
                        "可以绑定现有浏览器，也可以前往控制台创建。",
                      )}
                </p>
              )}
            </div>
          );
        })}
        {edit && (
          <div className={styles.stack}>
            {fresh && (
              <p role="status">
                {copy(
                  "Archive the Fresh Schedules that still reference this Project before changing bindings.",
                  "更改绑定前，请先归档仍引用此项目的新线程排程。 ",
                )}{" "}
                {freshLink}
              </p>
            )}
            {edit === "environment" && (
              <ChoiceField
                label={copy("Environment source", "环境来源")}
                value={source}
                onValueChange={(value) => {
                  setSource(value);
                  setSelection("");
                }}
                options={[
                  {
                    value: "environment",
                    label: copy("Existing instance", "现有实例"),
                  },
                  {
                    value: "template",
                    label: copy("Create from a template", "使用模板创建"),
                  },
                ]}
              />
            )}
            <ResourcePicker
              label={copy("New binding", "新绑定")}
              kind={
                edit === "browser"
                  ? "browser"
                  : source === "template"
                    ? "template"
                    : "environment"
              }
              account={claw.service_account_id}
              value={selection}
              onChange={setSelection}
              optional={edit === "browser"}
            />
            <div className={styles.actions}>
              <Button variant="ghost" onClick={() => setEdit(null)}>
                {copy("Cancel", "取消")}
              </Button>
              {!fresh &&
                (selection || (edit === "browser" && linked)) &&
                (source === "template" || selection !== (linked ?? "")) &&
                (linked ? (
                  <Confirm
                    title={copy("Change Project binding?", "更改项目绑定？")}
                    description={copy(
                      "Files and browser profiles stay with the original resources. Existing Runs keep captured bindings.",
                      "文件和浏览器配置保留在原资源中。已有运行继续使用其捕获的绑定。",
                    )}
                    subject={claw.name}
                    trigger={copy("Change binding", "更改绑定")}
                    action={rebind}
                    onSuccess={changed}
                  />
                ) : (
                  <Button
                    disabled={write.isPending}
                    onClick={() => void rebind().then(changed, () => undefined)}
                  >
                    {copy("Bind resource", "绑定资源")}
                  </Button>
                ))}
            </div>
            {write.isError && <ErrorNotice error={write.error} />}
          </div>
        )}
        {(environmentChanged || project.unauthorized_reply_connectors > 0) && (
          <p role="status">
            {copy(
              "Reauthorize connector replies for the current Environment.",
              "请为当前环境重新授权连接器回复。 ",
            )}{" "}
            <ClawLink id={claw.id} page="connectors">
              {copy("Open Connectors", "打开连接器")}
            </ClawLink>
          </p>
        )}
      </FormSection>
      <FormSection title={copy("Agent revision", "智能体修订版")}>
        <p>
          {snapshot.agent_revision_outdated
            ? copy(
                "A newer default revision is available.",
                "有新的默认修订版可用。",
              )
            : copy(
                "The pinned revision is current.",
                "已固定的修订版是最新版本。",
              )}
        </p>
        <div className={styles.actions}>
          <code>{claw.agent_revision_id}</code>
          {snapshot.agent_revision_outdated && (
            <Confirm
              title={copy("Sync Agent revision?", "同步智能体修订版？")}
              subject={claw.name}
              description={copy(
                "Future Claw admissions use the new revision. Existing Runs and native schedules keep their captures.",
                "后续 Claw 工作将使用新修订版。已有运行和原生排程不受影响。",
              )}
              trigger={copy("Sync revision", "同步修订版")}
              action={() =>
                write.mutateAsync({
                  path: `${path}/agent-revision/sync`,
                  method: "POST",
                  body: {},
                })
              }
            />
          )}
        </div>
      </FormSection>
      <FormSection title={copy("Platform CLIs", "平台命令行工具")}>
        <div className={styles.actions}>
          {Object.entries(project.cli_versions ?? {}).map(([name, version]) => (
            <code key={name}>
              {name} {version}
            </code>
          ))}
          {!project.cli_versions && (
            <span className={styles.muted}>
              {copy("Not checked", "尚未检查")}
            </span>
          )}
          {project.cli_update_state && (
            <Status state={project.cli_update_state} />
          )}
          {claw.environment_id && project.cli_update_state !== "queued" && (
            <Confirm
              title={copy("Queue CLI update?", "将命令行工具更新加入队列？")}
              subject={claw.name}
              description={copy(
                "The tools update in the Project Environment on the next eligible Main Run.",
                "工具会在下一次符合条件的主线程运行中更新。",
              )}
              trigger={copy("Update CLIs", "更新命令行工具")}
              action={() =>
                write.mutateAsync({
                  path: `${path}/project/cli-update`,
                  method: "POST",
                  body: {},
                })
              }
            />
          )}
        </div>
        {project.cli_update_error && (
          <p className={styles.error}>{project.cli_update_error}</p>
        )}
      </FormSection>
      <FormSection
        title={copy("Archive Claw", "归档 Claw")}
        description={copy(
          "Stop new Claw work without deleting native resources or existing records.",
          "停止新的 Claw 工作，不删除原生资源或已有记录。 ",
        )}
      >
        {fresh ? (
          <p>
            {copy(
              "Archive the associated Fresh Schedules first.",
              "请先归档关联的新线程排程。 ",
            )}{" "}
            {freshLink}
          </p>
        ) : (
          <Confirm
            title={copy("Archive this Claw?", "归档此 Claw？")}
            subject={claw.name}
            danger
            description={copy(
              "Intake and Claw automation stop. Native Threads, Runs, and Project resources remain in Console.",
              "消息接收和 Claw 自动化将停止。原生线程、运行和项目资源仍保留在控制台。 ",
            )}
            trigger={copy("Archive Claw", "归档 Claw")}
            action={() =>
              write.mutateAsync({ path, method: "DELETE", body: null })
            }
            onSuccess={archived}
          />
        )}
      </FormSection>
    </>
  );
}

interface CheckResult {
  state: string;
  reason?: string;
  http_status?: number;
}

export function ConnectorsPage() {
  const { params } = useSelection();
  const id = params.get("connector");
  return (
    <ClawScope>
      {(claw, picker) =>
        id ? (
          <ConnectorRoute key={id} claw={claw} id={id} />
        ) : (
          <ConnectorList key={claw.id} claw={claw} picker={picker} />
        )
      }
    </ClawScope>
  );
}

function replyState(row: Connector, claw: Claw) {
  return row.reply_reauthorization_required ||
    (row.reply_environment_id &&
      row.reply_environment_id !== claw.environment_id)
    ? "reauthorize needed"
    : row.reply_environment_id
      ? "authorized"
      : "off";
}
function credentialState(row: Connector) {
  return row.credential_configured ? "saved" : "not configured";
}

function ConnectorList({
  claw,
  picker,
}: {
  claw: Claw;
  picker: React.ReactNode;
}) {
  const { copy } = useCopy();
  const { can } = useAccess();
  const { select } = useSelection();
  const [pages, setPages] = React.useState<string[]>([]);
  const query = useRead<Collection<Connector>>(
    url(`${clawPath(claw.id)}/connectors`, { cursor: pages.at(-1) }),
    5000,
  );
  const add = (
    <Button onClick={() => select({ connector: "new" })}>
      {copy("New connector", "新建连接器")}
    </Button>
  );
  return (
    <Page
      title={copy("Connectors", "连接器")}
      titleAction={picker}
      description={copy(
        "Control platform intake, credentials, and authorization for replies.",
        "管理平台接收、凭据和回复授权。",
      )}
      actions={can("admin") && !!query.data?.items.length ? add : undefined}
    >
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPage error={query.error} />
      ) : !query.data.items.length ? (
        <Empty
          title={copy("No connectors yet", "还没有连接器")}
          description={copy(
            "Connect Lark / Feishu or a GitHub event forwarder.",
            "连接飞书 / Lark 或 GitHub 事件转发器。",
          )}
          action={can("admin") && add}
        />
      ) : (
        <>
          <ResourceTable
            caption={copy("Connectors", "连接器")}
            items={query.data.items}
            columns={[
              {
                label: copy("Name", "名称"),
                tone: "primary",
                render: (row) => (
                  <ClawLink
                    id={claw.id}
                    page="connectors"
                    query={{ connector: row.id }}
                  >
                    {row.name}
                  </ClawLink>
                ),
              },
              {
                label: copy("Platform", "平台"),
                render: (row) =>
                  row.kind === "github"
                    ? "GitHub"
                    : copy("Lark / Feishu", "飞书 / Lark"),
              },
              {
                label: copy("State", "状态"),
                render: (row) => (
                  <Status state={row.enabled ? "enabled" : "disabled"} />
                ),
              },
              {
                label: copy("Credential", "凭据"),
                render: (row) => <Status state={credentialState(row)} />,
              },
              {
                label: copy("Replies", "回复"),
                render: (row) => <Status state={replyState(row, claw)} />,
              },
              {
                label: copy("Listener", "监听状态"),
                render: (row) =>
                  row.listener_status ? (
                    <Status state={row.listener_status} />
                  ) : (
                    "—"
                  ),
              },
              {
                label: copy("Last received", "最近接收"),
                render: (row) => <DateTime value={row.last_event_at} />,
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
    </Page>
  );
}

function ConnectorRoute({ claw, id }: { claw: Claw; id: string }) {
  const { can } = useAccess();
  const { copy } = useCopy();
  const { select } = useSelection();
  const query = useRead<Connector>(
    id === "new"
      ? null
      : `${clawPath(claw.id)}/connectors/${encodeURIComponent(id)}`,
    5000,
  );
  const close = () => select({ connector: null });
  if (id === "new")
    return can("admin") ? (
      <ConnectorEditor claw={claw} close={close} />
    ) : (
      <ErrorPage
        error={
          new Error(
            copy(
              "Connector administration is not permitted.",
              "没有管理连接器的权限。",
            ),
          )
        }
      />
    );
  if (query.isPending) return <Loading page />;
  if (query.isError) return <ErrorPage error={query.error} />;
  return <ConnectorEditor claw={claw} row={query.data} close={close} />;
}

function ConnectorEditor({
  claw,
  row,
  close,
}: {
  claw: Claw;
  row?: Connector;
  close: () => void;
}) {
  const { copy } = useCopy();
  const toast = useToast();
  const { can } = useAccess();
  const { workspaceId } = useApp();
  const write = useWrite<Connector>();
  const check = useWrite<CheckResult>();
  const path = `${clawPath(claw.id)}/connectors${row ? `/${row.id}` : ""}`;
  const config = row?.config;
  const [kind, setKind] = React.useState(row?.kind ?? "github");
  const [account, setAccount] = React.useState(
    String(config?.principal_id ?? ""),
  );
  const [domain, setDomain] = React.useState(
    String(config?.domain ?? "https://open.feishu.cn"),
  );
  const [direct, setDirect] = React.useState(config?.direct_messages !== false);
  const [enabled, setEnabled] = React.useState(row?.enabled ?? true);
  const [reply, setReply] = React.useState(
    !!claw.environment_id && row?.reply_environment_id === claw.environment_id,
  );
  const [replyChanged, setReplyChanged] = React.useState(false);
  const allowed = can("admin");
  const back = url(appPath(workspaceId!, "connectors"), { claw: claw.id });
  const endpoint = `${window.location.origin}/api/v1/apps/a13n.claw${path}/input`;
  const reason: Record<string, string> = {
    provider_unreachable: copy(
      "Could not reach the provider.",
      "无法连接服务提供方。",
    ),
    check_region_and_credentials: copy(
      "Check the provider region, App ID and App secret.",
      "请检查平台区域、应用 ID 和应用密钥。",
    ),
    invalid_repository: copy(
      "Use an allowlisted repository in owner/repo form.",
      "请按 owner/repo 格式填写允许的仓库。",
    ),
    provider_auth_rejected: copy(
      "The provider rejected the credential.",
      "平台拒绝了该凭据。",
    ),
    repository_not_found_or_forbidden: copy(
      "The repository is missing or the credential cannot access it.",
      "仓库不存在，或该凭据无权访问。",
    ),
    credential_unavailable: copy(
      "The stored credential could not be opened.",
      "无法读取已保存的凭据。",
    ),
  };
  const label =
    kind === "github" ? "GitHub" : copy("Lark / Feishu", "飞书 / Lark");
  const rail = row && (
    <Rail>
      <RailSection title={copy("Connection status", "连接状态")}>
        <RailRow label={copy("Credential", "凭据")}>
          <Status state={credentialState(row)} />
        </RailRow>
        <RailRow label={copy("Replies", "回复")}>
          <Status state={replyState(row, claw)} />
        </RailRow>
        {row.listener_status && (
          <RailRow label={copy("Listener", "监听状态")}>
            <Status state={row.listener_status} />
          </RailRow>
        )}
        <RailRow label={copy("Last received", "最近接收")}>
          <DateTime value={row.last_event_at} />
        </RailRow>
      </RailSection>
    </Rail>
  );
  return (
    <DetailPage
      back={back}
      backLabel={copy("Connectors", "连接器")}
      header={
        <DetailHeader
          name={row?.name ?? copy("New connector", "新建连接器")}
          description={label}
          resourceKey={row?.id}
          status={
            row && <Status state={row.enabled ? "enabled" : "disabled"} />
          }
        />
      }
    >
      <DetailLayout rail={rail}>
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const body = {
              name: field(form, "name"),
              kind,
              enabled,
              config:
                kind === "github"
                  ? {
                      principal_id: account,
                      channels: items(form, "channels"),
                      senders: items(form, "senders"),
                    }
                  : {
                      app_id: field(form, "app"),
                      bot_open_id: field(form, "bot"),
                      domain,
                      groups: items(form, "groups"),
                      unmentioned_groups: items(form, "unmentioned"),
                      direct_messages: direct,
                      senders: items(form, "senders"),
                    },
              credential: field(form, "credential") || undefined,
              ...(replyChanged || !row
                ? { reply_environment_id: reply ? claw.environment_id : null }
                : {}),
            };
            write.mutate(
              { path, method: row ? "PUT" : "POST", body },
              {
                onSuccess: () => {
                  toast.add({ title: copy("Connector saved", "连接器已保存") });
                  close();
                },
              },
            );
          }}
        >
          <FormSection title={copy("Basic information", "基本信息")}>
            <FormField label={copy("Name", "名称")}>
              <Input
                name="name"
                defaultValue={row?.name}
                required
                maxLength={200}
                readOnly={!allowed}
              />
            </FormField>
            {!row && (
              <ChoiceField
                label={copy("Platform", "平台")}
                value={kind}
                onValueChange={(value) =>
                  setKind(value === "lark" ? "lark" : "github")
                }
                options={[
                  { value: "github", label: "GitHub" },
                  {
                    value: "lark",
                    label: copy("Lark / Feishu", "飞书 / Lark"),
                  },
                ]}
              />
            )}
            <FormField label={copy("Enabled", "启用")}>
              <Switch
                checked={enabled}
                onCheckedChange={setEnabled}
                disabled={!allowed}
              />
            </FormField>
          </FormSection>
          <FormSection
            title={copy("Intake", "消息接收")}
            description={
              kind === "github"
                ? copy(
                    "Forward GitHub events through your integration or GitHub Actions. Authenticate with the selected Service account and send the workspace ID in X-Workspace-ID.",
                    "通过自有集成或 GitHub Actions 转发事件。使用所选服务账号认证，并在 X-Workspace-ID 中发送工作区 ID。",
                  )
                : copy(
                    "Enable long-connection event delivery for im.message.receive_v1 in the Lark / Feishu developer console. Approve the required bot permissions and publish the app.",
                    "在飞书 / Lark 开发者控制台启用长连接并订阅 im.message.receive_v1，批准机器人所需权限后发布应用。",
                  )
            }
          >
            {kind === "github" ? (
              <>
                <ResourcePicker
                  label={copy("Integration service account", "集成服务账号")}
                  kind="account"
                  value={account}
                  onChange={setAccount}
                  disabled={!allowed}
                />
                {row && (
                  <div className={styles.stack}>
                    <FormField label={copy("Intake endpoint", "接收端点")}>
                      <Input readOnly value={endpoint} />
                    </FormField>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() =>
                        void navigator.clipboard.writeText(endpoint).then(
                          () =>
                            toast.add({
                              title: copy("Endpoint copied", "接收端点已复制"),
                            }),
                          () =>
                            toast.add({
                              title: copy(
                                "Select and copy the endpoint manually",
                                "请手动选择并复制端点",
                              ),
                            }),
                        )
                      }
                    >
                      {copy("Copy endpoint", "复制端点")}
                    </Button>
                  </div>
                )}
                <FormField
                  label={copy(
                    "Allowed repositories (owner/repo)",
                    "允许的仓库（owner/repo）",
                  )}
                >
                  <Textarea
                    name="channels"
                    rows={3}
                    required
                    readOnly={!allowed}
                    defaultValue={list(config, "channels")}
                  />
                </FormField>
              </>
            ) : (
              <>
                <FormField label={copy("App ID", "应用 ID")}>
                  <Input
                    name="app"
                    required
                    readOnly={!allowed}
                    defaultValue={String(config?.app_id ?? "")}
                  />
                </FormField>
                <FormField label={copy("Bot open ID", "机器人 Open ID")}>
                  <Input
                    name="bot"
                    required
                    readOnly={!allowed}
                    defaultValue={String(config?.bot_open_id ?? "")}
                  />
                </FormField>
                <ChoiceField
                  label={copy("Region", "区域")}
                  disabled={!allowed}
                  value={domain}
                  onValueChange={setDomain}
                  options={[
                    {
                      value: "https://open.feishu.cn",
                      label: copy("Feishu", "飞书"),
                    },
                    { value: "https://open.larksuite.com", label: "Lark" },
                  ]}
                />
                <FormField
                  label={copy("Allow direct messages", "允许私聊消息")}
                >
                  <Checkbox
                    checked={direct}
                    onCheckedChange={setDirect}
                    disabled={!allowed}
                  />
                </FormField>
                <FormField
                  label={copy(
                    "Allowed group IDs (optional)",
                    "允许的群组 ID（可选）",
                  )}
                >
                  <Textarea
                    name="groups"
                    rows={2}
                    readOnly={!allowed}
                    defaultValue={list(config, "groups")}
                  />
                </FormField>
                <FormField
                  label={copy(
                    "Groups that do not require mentioning the bot (optional)",
                    "无需提及机器人的群组（可选）",
                  )}
                >
                  <Textarea
                    name="unmentioned"
                    rows={2}
                    readOnly={!allowed}
                    defaultValue={list(config, "unmentioned_groups")}
                  />
                </FormField>
              </>
            )}
            <FormField
              label={
                kind === "github"
                  ? copy("Allowed GitHub usernames", "允许的 GitHub 用户名")
                  : copy("Allowed sender open IDs", "允许的发送人 Open ID")
              }
              description={copy(
                "Separate entries with spaces or new lines.",
                "用空格或换行分隔多个条目。",
              )}
            >
              <Textarea
                name="senders"
                rows={3}
                required
                readOnly={!allowed}
                defaultValue={list(config, "senders")}
              />
            </FormField>
          </FormSection>
          <FormSection title={copy("Credentials and replies", "凭据与回复")}>
            {allowed && (
              <FormField
                label={
                  kind === "github"
                    ? copy("GitHub token (optional)", "GitHub 令牌（可选）")
                    : copy("App secret (optional)", "应用密钥（可选）")
                }
                description={copy(
                  "Write-only. Leave blank to keep the saved value.",
                  "凭据只写。留空可保留已保存值。",
                )}
              >
                <Input
                  type="password"
                  name="credential"
                  autoComplete="new-password"
                  maxLength={8192}
                />
              </FormField>
            )}
            <FormField
              label={copy(
                "Authorize replies in the current Environment",
                "授权在当前环境中回复",
              )}
              description={
                !claw.environment_id
                  ? copy(
                      "Bind a Project Environment first.",
                      "请先绑定项目环境。",
                    )
                  : undefined
              }
            >
              <Switch
                disabled={!allowed || !claw.environment_id}
                checked={reply}
                onCheckedChange={(value) => {
                  setReply(value);
                  setReplyChanged(true);
                }}
              />
            </FormField>
            {allowed && row && (
              <div className={styles.actions}>
                <Button
                  type="button"
                  variant="outline"
                  disabled={check.isPending}
                  onClick={() =>
                    check.mutate({
                      path: `${path}/check`,
                      method: "POST",
                      body: {},
                    })
                  }
                >
                  {copy("Check saved connection", "检查已保存的连接")}
                </Button>
                {row.credential_configured && (
                  <Confirm
                    title={copy(
                      "Remove saved credential?",
                      "移除已保存的凭据？",
                    )}
                    subject={row.name}
                    danger
                    description={copy(
                      "Claw stops using this credential for platform calls. Rotate or revoke it at the provider if needed.",
                      "Claw 将停止使用该凭据调用平台。需要时，请在平台侧轮换或撤销它。",
                    )}
                    trigger={copy("Remove credential", "移除凭据")}
                    action={() =>
                      write.mutateAsync({
                        path,
                        method: "PUT",
                        body: {
                          name: row.name,
                          kind: row.kind,
                          enabled: row.enabled,
                          config: row.config,
                          credential: null,
                        },
                      })
                    }
                    onSuccess={() => {
                      check.reset();
                      toast.add({
                        title: copy(
                          "Saved credential removed",
                          "已移除保存的凭据",
                        ),
                      });
                    }}
                  />
                )}
              </div>
            )}
            {check.data && (
              <p role="status">
                <Status state={check.data.state} />{" "}
                {check.data.reason
                  ? (reason[check.data.reason] ??
                    copy(
                      "The provider did not confirm connectivity.",
                      "平台未确认连接。",
                    ))
                  : ""}{" "}
                {check.data.http_status ? `HTTP ${check.data.http_status}` : ""}
              </p>
            )}
            {check.isError && <ErrorNotice error={check.error} />}
          </FormSection>
          {write.isError && <ErrorNotice error={write.error} />}
          {allowed && (
            <FormActions
              pending={write.isPending}
              disabled={kind === "github" && !account}
              onCancel={close}
              label={copy("Save connector", "保存连接器")}
              leading={
                row && (
                  <Confirm
                    title={copy("Delete this connector?", "删除此连接器？")}
                    subject={row.name}
                    danger
                    description={copy(
                      "Intake and replies stop. Existing deliveries remain available.",
                      "停止接收和回复，已有投递记录保留。",
                    )}
                    trigger={copy("Delete connector", "删除连接器")}
                    action={() =>
                      write.mutateAsync({ path, method: "DELETE", body: null })
                    }
                    onSuccess={close}
                  />
                )
              }
            />
          )}
        </form>
      </DetailLayout>
    </DetailPage>
  );
}

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
  const fresh = params.get("mode") === "fresh";
  const [pages, setPages] = React.useState<string[]>([]);
  const query = useRead<Collection<Schedule | FreshSchedule>>(
    url(`${clawPath(claw.id)}/${fresh ? "fresh-schedules" : "schedules"}`, {
      cursor: pages.at(-1),
    }),
    10000,
  );
  const add = can("write") && (
    <Button
      disabled={fresh && !claw.environment_id}
      onClick={() => select({ schedule: "new" })}
    >
      {copy("New schedule", "新建排程")}
    </Button>
  );
  return (
    <Page
      title={copy("Schedules", "排程")}
      titleAction={picker}
      actions={query.data?.items.length ? add : undefined}
      description={copy(
        "Schedule work for Main or start a new native Thread on each occurrence.",
        "将工作安排给主线程，或在每次触发时启动一个新的原生线程。",
      )}
      toolbar={
        <SegmentedControl
          label={copy("Schedule mode", "排程模式")}
          value={fresh ? "fresh" : "main"}
          onValueChange={(value) => {
            setPages([]);
            select({ mode: value === "fresh" ? value : null });
          }}
          options={[
            { value: "main", label: copy("Main", "主线程") },
            { value: "fresh", label: copy("Fresh Threads", "新线程") },
          ]}
        />
      }
    >
      {fresh && !claw.environment_id && (
        <p className={styles.muted}>
          {copy(
            "Bind a Project Environment before creating a Fresh Schedule.",
            "创建新线程排程前，请先绑定项目环境。",
          )}
        </p>
      )}
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPage error={query.error} />
      ) : query.data.items.length === 0 ? (
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
            items={query.data.items}
            columns={[
              {
                label: copy("Name", "名称"),
                tone: "primary",
                render: (row) =>
                  "state" in row ? (
                    <ResourceLink path={`schedules/${row.id}`}>
                      {row.name}
                    </ResourceLink>
                  ) : (
                    <ClawLink
                      id={claw.id}
                      page="schedules"
                      query={{ schedule: row.id }}
                    >
                      {row.name}
                    </ClawLink>
                  ),
              },
              {
                label: copy("State", "状态"),
                render: (row) => (
                  <Status
                    state={
                      "state" in row
                        ? row.state
                        : row.enabled
                          ? "enabled"
                          : "paused"
                    }
                  />
                ),
              },
              {
                label: copy("Next occurrence", "下次触发"),
                render: (row) => <DateTime value={row.next_at} />,
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
        fresh={false}
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
          ? copy("Edit Main schedule", "编辑主线程排程")
          : fresh
            ? copy("New Fresh Schedule", "新建新线程排程")
            : copy("New Main schedule", "新建主线程排程")
      }
      back={back}
      backLabel={copy("Schedules", "排程")}
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          const body = {
            name: field(form, "name"),
            prompt: field(form, "prompt"),
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
          const path = `${clawPath(claw.id)}/${fresh ? "fresh-schedules" : `schedules${row ? `/${row.id}` : ""}`}`;
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
            <FormField label={copy("Change the schedule rule", "更改排程规则")}>
              <Switch checked={changeRule} onCheckedChange={setChangeRule} />
            </FormField>
          )}
          {changeRule && (
            <>
              <FormField label={copy("Frequency", "频率")}>
                <Select
                  value={mode}
                  onValueChange={(value) => value && setMode(value)}
                >
                  <SelectTrigger>
                    <SelectValue>
                      {mode === "cron"
                        ? copy("Recurring", "重复执行")
                        : copy("Once", "仅一次")}
                    </SelectValue>
                  </SelectTrigger>
                  <SelectPopup>
                    <SelectItem value="cron">
                      {copy("Recurring", "重复执行")}
                    </SelectItem>
                    <SelectItem value="once">
                      {copy("Once", "仅一次")}
                    </SelectItem>
                  </SelectPopup>
                </Select>
              </FormField>
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
  const [tab, setTab] = React.useState("occurrences");
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
      tab={tab}
      onTabChange={setTab}
      tabs={[
        { value: "occurrences", label: copy("Occurrences", "触发记录") },
        { value: "task", label: copy("Task", "任务") },
      ]}
      header={
        <DetailHeader
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
                  title={copy("Delete Main schedule?", "删除主线程排程？")}
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
        {tab === "task" ? (
          <pre className={styles.payload}>{row.prompt}</pre>
        ) : query.isPending ? (
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
                  label: copy("Main Run", "主线程运行"),
                  render: (item) =>
                    item.run_id ? (
                      <ThreadLink
                        thread={claw.main_thread_id}
                        session={claw.main_session_id}
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
      </DetailLayout>
    </DetailPage>
  );
}
