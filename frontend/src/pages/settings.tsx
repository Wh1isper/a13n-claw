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
  ListRow,
  ListRows,
  Page,
  Rail,
  RailRow,
  RailSection,
  React,
  ReadOnlyField,
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
} from "../data";
import {
  ClawLink,
  ClawScope,
  DateTime,
  InputValue,
  Pager,
  ResourceLink,
  Status,
  ThreadLink,
} from "../primitives";
import { useCopy } from "../copy";
import styles from "../app.module.css";
import { Icon } from "../ui/icons";

import { field, items, list, ResourcePicker } from "./form-common";

export function ConfigurationPage() {
  const { copy } = useCopy();
  const { params, select } = useSelection();
  const tab = params.get("section") === "project" ? "project" : "general";
  const done = (claw: Claw) => select({ new: null, claw: claw.id });
  if (params.has("new"))
    return <ClawEditor done={done} cancel={() => select({ new: null })} />;
  return (
    <ClawScope setup={<ClawEditor done={done} />}>
      {(claw, picker) => (
        <div key={claw.id} className={styles.settingsPage}>
          <div className={styles.settingsBar}>
            <div className={styles.actions}>
              {picker}
              <code className={styles.key}>{claw.id}</code>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => select({ new: "1" })}
            >
              {copy("New Claw", "新建 Claw")}
            </Button>
          </div>
          <SegmentedControl
            label={copy("Claw settings sections", "Claw 设置分区")}
            value={tab}
            onValueChange={(value) =>
              select({ section: value === "project" ? "project" : null })
            }
            options={[
              {
                value: "general",
                label: copy("General & Inbox", "常规与收件箱"),
              },
              {
                value: "project",
                label: copy("Project & Lifecycle", "项目与生命周期"),
              },
            ]}
          />
          <div hidden={tab !== "general"}>
            <ClawEditor claw={claw} />
          </div>
          <div hidden={tab !== "project"}>
            <ProjectResources
              claw={claw}
              archived={() => select({ claw: null })}
            />
          </div>
        </div>
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
        {claw && (
          <ReadOnlyField
            label={copy("Automation service account", "自动化服务账号")}
          >
            <ResourceLink path="settings/service-accounts">
              {claw.service_account_id}
            </ResourceLink>
          </ReadOnlyField>
        )}
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
          data-field-layout="setting"
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
        <FormField
          data-field-layout="setting"
          label={copy("Unread limit", "未读上限")}
        >
          <Input
            name="unread"
            type="number"
            required
            min={1}
            max={10000}
            defaultValue={claw?.unread_limit ?? 500}
          />
        </FormField>
        <FormField
          data-field-layout="setting"
          label={copy("Read history limit", "已读保留上限")}
        >
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
    <div className={styles.detailSections}>
      <Section
        title={copy("Project resources", "项目资源")}
        description={copy(
          "Native resources remain owned by Service.",
          "原生资源由 Service 管理。 ",
        )}
      >
        <ListRows>
          <ListRow
            icon={<Icon name="claw" />}
            name={copy("Agent revision", "智能体修订版")}
            secondary={claw.agent_revision_id ?? "—"}
            control={
              snapshot.agent_revision_outdated && <Status state="outdated" />
            }
            actions={
              <ResourceLink path={`agents/${claw.agent_id}`}>
                {copy("Open", "打开")}
              </ResourceLink>
            }
          />
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
            const secondary =
              guidance === "resume"
                ? copy(
                    "Stopped · resume the same instance",
                    "已停止 · 请恢复同一实例",
                  )
                : guidance === "replace"
                  ? failure ||
                    copy("Unavailable · needs replacement", "不可用 · 需要更换")
                  : name || copy("Not configured", "未配置");
            return (
              <ListRow
                key={kind}
                className={styles.resourceRow}
                icon={<Icon name={env ? "terminal" : "browser"} />}
                name={title}
                secondary={secondary}
                control={<Status state={state} />}
                actions={
                  <div className={styles.actions}>
                    <ResourceLink
                      path={env ? "environments/instances" : "browsers"}
                    >
                      {copy("Open", "打开")}
                    </ResourceLink>
                    {guidance !== "resume" && (
                      <Button
                        variant="outline"
                        size="sm"
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
                }
              />
            );
          })}
          <ListRow
            icon={<Icon name="memory" />}
            name={copy("Memory Space", "记忆空间")}
            secondary={claw.memory_space_id}
            actions={
              <ResourceLink path={`memory-spaces/${claw.memory_space_id}`}>
                {copy("Open", "打开")}
              </ResourceLink>
            }
          />
          <ListRow
            icon={<Icon name="claw" />}
            name={copy("Automation account", "自动化账号")}
            secondary={claw.service_account_id}
            actions={
              <ResourceLink path="settings/service-accounts">
                {copy("Open", "打开")}
              </ResourceLink>
            }
          />
        </ListRows>
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
      </Section>
      <Section title={copy("Agent revision", "智能体修订版")}>
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
      </Section>
      <Section title={copy("Platform CLIs", "平台命令行工具")}>
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
      </Section>
      <Section
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
      </Section>
    </div>
  );
}
