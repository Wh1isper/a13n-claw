import {
  Button,
  DetailHeader,
  DetailPage,
  Empty,
  ErrorPage,
  FormActions,
  FormField,
  FormSection,
  Input,
  Loading,
  Page,
  React,
  ResourceTable,
  Textarea,
  useAccess,
  useApp,
} from "@a13n/console";
import {
  appPath,
  clawPath,
  json,
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
  type Schedule,
  type ResourceKind,
} from "./data";
import {
  ClawScope,
  DateTime,
  Icon,
  InputValue,
  Pager,
  ResourceLink,
  Status,
  ThreadLink,
} from "./primitives";
import styles from "./app.module.css";

const field = (form: FormData, name: string) =>
  String(form.get(name) ?? "").trim();
const items = (form: FormData, name: string) =>
  field(form, name)
    .split(/\s|,/)
    .map((item) => item.trim())
    .filter(Boolean);
const list = (config: Connector["config"] | undefined, key: string) =>
  Array.isArray(config?.[key]) ? config[key].join("\n") : "";

export function ConfigurationPage() {
  const { params, select } = useSelection();
  if (params.get("new"))
    return (
      <ClawEditor
        done={(claw) => select({ new: null, claw: claw?.id ?? null })}
      />
    );
  return (
    <ClawScope
      setup={<ClawEditor done={(claw) => select({ claw: claw?.id ?? null })} />}
    >
      {(claw, picker) => (
        <ClawEditor
          key={claw.id}
          claw={claw}
          action={
            <div className={styles.actions}>
              {picker}
              <Button variant="outline" onClick={() => select({ new: "1" })}>
                <Icon name="plus" /> New Claw
              </Button>
            </div>
          }
        />
      )}
    </ClawScope>
  );
}
function ResourcePicker({
  label,
  name,
  kind,
  account,
  value,
  onChange,
  optional = false,
  disabled = false,
}: {
  label: string;
  name: string;
  kind: ResourceKind;
  account?: string;
  value: string;
  onChange: (value: string) => void;
  optional?: boolean;
  disabled?: boolean;
}) {
  const choices = useProjectResources(kind, !disabled, account);
  const rows = choices.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <div>
      <FormField label={label} disabled={disabled}>
        <select
          className={styles.select}
          name={name}
          required={!optional}
          value={value}
          onChange={(event) => onChange(event.currentTarget.value)}
        >
          <option value="">
            {optional
              ? "None"
              : disabled
                ? "Select a service account first"
                : choices.isPending
                  ? "Loading…"
                  : rows.length
                    ? "Select a resource"
                    : "No resources available"}
          </option>
          {value && !rows.some((row) => row.id === value) && (
            <option value={value}>{value}</option>
          )}
          {rows.map((row) => (
            <option key={row.id} value={row.id}>
              {row.name}
            </option>
          ))}
        </select>
      </FormField>
      {choices.hasNextPage && (
        <Button
          type="button"
          variant="ghost"
          disabled={choices.isFetchingNextPage}
          onClick={() => void choices.fetchNextPage()}
        >
          Load more
        </Button>
      )}
      {choices.isError && (
        <Button
          type="button"
          variant="ghost"
          onClick={() => void choices.refetch()}
        >
          Retry loading {label.toLowerCase()}
        </Button>
      )}
    </div>
  );
}

function ProjectResources({ claw }: { claw: Claw }) {
  const overview = useRead<Overview>(clawPath(claw.id), 10000);
  const { can } = useAccess();
  const save = useWrite<Claw>();
  const updateCli = useWrite<{ state: string }>();
  const [editing, setEditing] = React.useState<
    "environment" | "browser" | null
  >(null);
  const [template, setTemplate] = React.useState(false);
  const [selection, setSelection] = React.useState("");
  const project = overview.data?.project;
  const saved = overview.data?.claw ?? claw;
  const current =
    editing === "environment" ? saved.environment_id : saved.browser_id;
  const begin = (kind: "environment" | "browser") => {
    setEditing(kind);
    setTemplate(false);
    setSelection("");
    save.reset();
  };
  return (
    <div className={styles.form}>
      <FormSection
        title="Project"
        description="Connect the workspace resources this Claw uses across Runs."
      >
        {!saved.environment_id && (
          <p className={styles.muted}>
            Add an Environment to start Main. Incoming work stays in the Inbox.
          </p>
        )}
        <div className={styles.references}>
          <ResourceLink path={`agents/${saved.agent_id}`}>Agent</ResourceLink>
          <ResourceLink path={`memory-spaces/${saved.memory_space_id}`}>
            Memory Space
          </ResourceLink>
        </div>
        {(["environment", "browser"] as const).map((kind) => {
          const environment = kind === "environment";
          const title = environment ? "Environment" : "Browser";
          const linked = environment ? saved.environment_id : saved.browser_id;
          const name = environment
            ? project?.environment_name
            : project?.browser_name;
          const state = environment ? project?.environment : project?.browser;
          return (
            <div className={styles.actions} key={kind}>
              <strong>{title}</strong>
              <Status
                state={state ?? (linked ? "configured" : "not configured")}
              />
              {linked && <span>{name ?? linked}</span>}
              <ResourceLink
                path={environment ? "environments/instances" : "browsers"}
              >
                {environment ? "Environments" : "Browsers"}
              </ResourceLink>
              {can("admin") && (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => begin(kind)}
                >
                  {linked ? "Change" : "Add"} {title.toLowerCase()}
                </Button>
              )}
            </div>
          );
        })}
        {overview.isError && (
          <p className={styles.error} role="alert">
            {overview.error.message}
          </p>
        )}
        {editing && (
          <form
            className={styles.form}
            onSubmit={(event) => {
              event.preventDefault();
              if (editing === "environment" && !selection) return;
              const key =
                editing === "browser"
                  ? "browser_id"
                  : template
                    ? "environment_template_id"
                    : "environment_id";
              save.mutate(
                {
                  path: `${clawPath(claw.id)}/project`,
                  method: "PATCH",
                  body: { [key]: selection || null },
                },
                { onSuccess: () => setEditing(null) },
              );
            }}
          >
            {editing === "environment" && (
              <FormField label="Environment source">
                <select
                  className={styles.select}
                  value={template ? "template" : "existing"}
                  onChange={(event) => {
                    setTemplate(event.currentTarget.value === "template");
                    setSelection("");
                  }}
                >
                  <option value="existing">Existing Environment</option>
                  <option value="template">Create from template</option>
                </select>
              </FormField>
            )}
            <ResourcePicker
              name="resource"
              label={
                editing === "browser"
                  ? "Browser"
                  : template
                    ? "Environment template"
                    : "Environment"
              }
              kind={
                editing === "browser"
                  ? "browser"
                  : template
                    ? "template"
                    : "environment"
              }
              account={saved.service_account_id}
              value={selection}
              onChange={setSelection}
              optional={editing === "browser" && !!current}
            />
            {editing === "browser" && (
              <ResourceLink path="browsers">
                Create a Browser in Console
              </ResourceLink>
            )}
            {current && (
              <p className={styles.muted}>
                This changes future Runs. Existing files and browser profiles
                stay with their original resources.
              </p>
            )}
            {save.isError && (
              <p role="alert" className={styles.error}>
                {save.error.message}
              </p>
            )}
            <div className={styles.actions}>
              <Button
                type="submit"
                disabled={
                  save.isPending ||
                  (!selection && (editing === "environment" || !current)) ||
                  selection === current
                }
              >
                {!selection && editing === "browser"
                  ? "Disconnect Browser"
                  : "Save binding"}
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => setEditing(null)}
              >
                Cancel
              </Button>
            </div>
          </form>
        )}
      </FormSection>
      <FormSection
        title="Platform CLIs"
        description="Claw installs missing tools before Main runs. Updates apply at the next Main execution with this Environment."
      >
        <dl className={styles.facts}>
          <dt>GitHub CLI</dt>
          <dd>{project?.cli_versions?.gh ?? "Not installed yet"}</dd>
          <dt>Lark CLI</dt>
          <dd>{project?.cli_versions?.["lark-cli"] ?? "Not installed yet"}</dd>
        </dl>
        <div className={styles.actions}>
          {can("admin") && (
            <Button
              type="button"
              variant="outline"
              disabled={
                !saved.environment_id ||
                !project ||
                !!editing ||
                updateCli.isPending ||
                project.cli_update_state === "queued"
              }
              onClick={() =>
                updateCli.mutate({
                  path: `${clawPath(claw.id)}/project/cli-update`,
                  method: "POST",
                  body: {},
                })
              }
            >
              Update CLIs
            </Button>
          )}
          {project?.cli_update_state && (
            <Status state={project.cli_update_state} />
          )}
        </div>
        {updateCli.isError && (
          <p role="alert" className={styles.error}>
            {updateCli.error.message}
          </p>
        )}
      </FormSection>
    </div>
  );
}

function ClawEditor({
  claw,
  action,
  done,
}: {
  claw?: Claw;
  action?: React.ReactNode;
  done?: (claw?: Claw) => void;
}) {
  const save = useWrite<Claw>();
  const [template, setTemplate] = React.useState(false);
  const [agent, setAgent] = React.useState("");
  const [account, setAccount] = React.useState("");
  const [environment, setEnvironment] = React.useState("");
  const [browser, setBrowser] = React.useState("");
  const nextModelKey = React.useRef(claw?.worker_models.length ?? 0);
  const [models, setModels] = React.useState(() =>
    (claw?.worker_models ?? []).map((row, key) => ({ ...row, key })),
  );
  const updateModel = (
    key: number,
    field: "model" | "description",
    value: string,
  ) =>
    setModels((current) =>
      current.map((row) =>
        row.key === key ? { ...row, [field]: value } : row,
      ),
    );
  return (
    <Page
      title={claw ? "Claw settings" : "Set up Claw"}
      description="Configure the Project and Inbox behavior."
      titleAction={action}
    >
      {claw && <ProjectResources claw={claw} />}
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          const settings = {
            name: field(form, "name"),
            heartbeat_seconds: Number(field(form, "heartbeat")),
            unread_limit: Number(field(form, "unread")),
            read_limit: Number(field(form, "read")),
            worker_models: models.map(({ model, description }) => ({
              model,
              description: description.trim(),
            })),
          };
          const body = claw
            ? settings
            : {
                ...settings,
                agent_id: field(form, "agent"),
                service_account_id: field(form, "account"),
                browser_id: field(form, "browser") || null,
                ...(environment
                  ? {
                      [template ? "environment_template_id" : "environment_id"]:
                        environment,
                    }
                  : {}),
              };
          save.mutate(
            {
              path: claw ? `${clawPath(claw.id)}/settings` : "/claws",
              method: claw ? "PUT" : "POST",
              body,
            },
            { onSuccess: (result) => done?.(result) },
          );
        }}
      >
        <FormSection title="Identity">
          <FormField label="Name">
            <InputValue name="name" value={claw?.name} maxLength={200} />
          </FormField>
        </FormSection>
        {!claw && (
          <FormSection
            title="Project"
            description="Use resources from this workspace."
          >
            <ResourcePicker
              label="Agent"
              kind="agent"
              name="agent"
              value={agent}
              onChange={setAgent}
            />
            <ResourcePicker
              label="Workspace service account"
              kind="account"
              name="account"
              value={account}
              onChange={(value) => {
                setAccount(value);
                if (!template) setEnvironment("");
              }}
            />
            <FormField label="Environment source">
              <select
                className={styles.select}
                value={template ? "template" : "existing"}
                onChange={(event) => {
                  setTemplate(event.currentTarget.value === "template");
                  setEnvironment("");
                }}
              >
                <option value="existing">Existing Environment</option>
                <option value="template">Create from template</option>
              </select>
            </FormField>
            <ResourcePicker
              label={template ? "Environment template" : "Environment"}
              kind={template ? "template" : "environment"}
              account={template ? undefined : account}
              disabled={!template && !account}
              name="environment"
              value={environment}
              onChange={setEnvironment}
              optional
            />
            <ResourcePicker
              label="Browser (optional)"
              kind="browser"
              name="browser"
              value={browser}
              onChange={setBrowser}
              optional
            />
          </FormSection>
        )}
        <FormSection title="Inbox and heartbeat">
          <FormField label="Heartbeat interval (seconds)">
            <Input
              name="heartbeat"
              type="number"
              required
              min={0}
              max={86400}
              defaultValue={claw?.heartbeat_seconds ?? 300}
            />
          </FormField>
          <FormField label="Unread limit">
            <Input
              name="unread"
              type="number"
              required
              min={1}
              max={10000}
              defaultValue={claw?.unread_limit ?? 500}
            />
          </FormField>
          <FormField label="Read history limit">
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
          title="Worker models"
          description="Suggest models to Main and describe the work each suits."
        >
          {models.map((row) => (
            <div key={row.key} className={styles.form}>
              <ResourcePicker
                label="Model"
                kind="model"
                name="worker-model"
                value={row.model}
                onChange={(value) => updateModel(row.key, "model", value)}
              />
              <FormField label="Use for">
                <Input
                  required
                  maxLength={500}
                  value={row.description}
                  onChange={(event) =>
                    updateModel(
                      row.key,
                      "description",
                      event.currentTarget.value,
                    )
                  }
                />
              </FormField>
              <div className={styles.actions}>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() =>
                    setModels((current) =>
                      current.filter((item) => item.key !== row.key),
                    )
                  }
                >
                  Remove model
                </Button>
              </div>
            </div>
          ))}
          {models.length < 20 && (
            <div className={styles.actions}>
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  const key = nextModelKey.current++;
                  setModels((current) => [
                    ...current,
                    { key, model: "", description: "" },
                  ]);
                }}
              >
                <Icon name="plus" /> Add model
              </Button>
            </div>
          )}
        </FormSection>
        {save.isError && (
          <p role="alert" className={styles.error}>
            {save.error.message}
          </p>
        )}
        {save.isSuccess && claw && (
          <span role="status" className={styles.muted}>
            Settings saved
          </span>
        )}
        <FormActions
          pending={save.isPending}
          onCancel={done ? () => done() : undefined}
        />
      </form>
    </Page>
  );
}

export function ConnectorsPage() {
  return (
    <ClawScope>
      {(claw, picker) => (
        <ConnectorList key={claw.id} claw={claw} picker={picker} />
      )}
    </ClawScope>
  );
}
function ConnectorList({
  claw,
  picker,
}: {
  claw: Claw;
  picker: React.ReactNode;
}) {
  const { params, select } = useSelection();
  const editing = params.get("connector");
  const { can } = useAccess();
  const admin = can("admin");
  const [pages, setPages] = React.useState<string[]>([]);
  const path = `${clawPath(claw.id)}/connectors`;
  const query = useRead<Collection<Connector>>(
    editing ? null : url(path, { cursor: pages.at(-1) }),
    pages.length ? undefined : 15000,
  );
  const record = useRead<Connector>(
    editing && editing !== "new"
      ? `${path}/${encodeURIComponent(editing)}`
      : null,
    15000,
  );
  const close = () => select({ connector: null });
  if (editing === "new" && admin)
    return <ConnectorEditor key="new" claw={claw} close={close} />;
  if (editing && editing !== "new") {
    if (record.isPending) return <Loading page />;
    if (record.isError)
      return (
        <Page
          title="Connector"
          titleAction={
            <Button variant="outline" onClick={close}>
              Back to connectors
            </Button>
          }
        >
          <ErrorPage error={record.error} />
        </Page>
      );
    return (
      <ConnectorEditor
        key={editing}
        claw={claw}
        connector={record.data}
        close={close}
        readOnly={!admin}
      />
    );
  }
  if (editing === "new")
    return (
      <Empty
        title="Manage connectors"
        description="Workspace admins add connectors."
        action={
          <Button variant="outline" onClick={close}>
            Back to connectors
          </Button>
        }
      />
    );
  if (query.isPending) return <Loading page />;
  if (query.isError) return <ErrorPage error={query.error} />;
  const { items: rows, next_cursor: next } = query.data;
  const add = admin && (
    <Button
      variant={rows.length ? "outline" : "default"}
      onClick={() => select({ connector: "new" })}
    >
      <Icon name="plus" /> Add connector
    </Button>
  );
  return (
    <Page
      title="Connectors"
      description="Receive GitHub and Lark messages in Claw Inbox."
      titleAction={
        <div className={styles.actions}>
          {picker}
          {!!rows.length && add}
        </div>
      }
    >
      {rows.length ? (
        <>
          <ResourceTable
            caption="Connectors"
            items={rows}
            columns={[
              {
                label: "Connector",
                tone: "primary",
                render: (row) => (
                  <button
                    className={styles.rowAction}
                    onClick={() => select({ connector: row.id })}
                  >
                    {row.name}
                    <Icon name="next" />
                  </button>
                ),
              },
              {
                label: "Platform",
                render: (row) => (row.kind === "github" ? "GitHub" : "Lark"),
              },
              {
                label: "State",
                render: (row) => (
                  <Status state={row.enabled ? "enabled" : "disabled"} />
                ),
              },
              {
                label: "Last received",
                render: (row) => <DateTime value={row.last_event_at} />,
              },
            ]}
          />
          {(next || pages.length > 0) && (
            <Pager
              next={!!next}
              onNext={() => setPages([...pages, next!])}
              onPrevious={
                pages.length ? () => setPages(pages.slice(0, -1)) : undefined
              }
            />
          )}
        </>
      ) : (
        <Empty
          title="No connectors"
          description="Connect GitHub or Lark to receive messages."
          action={add}
        />
      )}
    </Page>
  );
}
function ConnectorEditor({
  claw,
  connector,
  close,
  readOnly = false,
}: {
  claw: Claw;
  connector?: Connector;
  close: () => void;
  readOnly?: boolean;
}) {
  const app = useApp();
  const { workspaceId } = app;
  const check = useWrite<{ state: string }>();
  const [saving, setSaving] = React.useState(false);
  const [saveError, setSaveError] = React.useState<string | null>(null);
  const path = `${clawPath(claw.id)}/connectors${connector ? `/${connector.id}` : ""}`;
  const credentialState = connector?.credential_configured
    ? "Saved"
    : connector?.credential_mounted
      ? "Mounted (legacy)"
      : "Not configured";
  const [kind, setKind] = React.useState(connector?.kind ?? "github");
  const config = connector?.config;
  const back = url(appPath(workspaceId!, "connectors"), { claw: claw.id });
  return (
    <DetailPage
      back={back}
      backLabel="Connectors"
      header={
        <DetailHeader
          name={connector?.name ?? "Add connector"}
          status={
            connector && (
              <Status state={connector.enabled ? "enabled" : "disabled"} />
            )
          }
        />
      }
    >
      {readOnly && connector ? (
        <FormSection title="Connection status">
          <dl className={styles.facts}>
            <dt>Platform</dt>
            <dd>{kind === "github" ? "GitHub" : "Lark"}</dd>
            <dt>Connector ID</dt>
            <dd>{connector.id}</dd>
            <dt>Credential</dt>
            <dd>{credentialState}</dd>
            {connector.listener_status && (
              <>
                <dt>Listener</dt>
                <dd>
                  <Status state={connector.listener_status} />
                </dd>
              </>
            )}
            <dt>Last received</dt>
            <dd>
              <DateTime value={connector.last_event_at} />
            </dd>
          </dl>
        </FormSection>
      ) : (
        <form
          className={styles.form}
          onSubmit={async (event) => {
            event.preventDefault();
            if (saving) return;
            setSaving(true);
            setSaveError(null);
            const form = new FormData(event.currentTarget);
            const senders = items(form, "senders");
            const settings =
              kind === "github"
                ? {
                    senders,
                    principal_id: field(form, "principal"),
                    channels: items(form, "channels"),
                  }
                : {
                    senders,
                    app_id: field(form, "app"),
                    bot_open_id: field(form, "bot"),
                    domain: field(form, "domain"),
                    groups: items(form, "groups"),
                    unmentioned_groups: items(form, "unmentioned"),
                    direct_messages: form.has("direct"),
                  };
            try {
              await json<Connector>(
                await app.request(path, {
                  method: connector ? "PUT" : "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    name: field(form, "name"),
                    kind,
                    enabled: form.has("enabled"),
                    config: settings,
                    credential: field(form, "credential") || undefined,
                    reply_environment_id: form.has("enable-replies")
                      ? claw.environment_id
                      : null,
                  }),
                }),
              );
              close();
            } catch (error) {
              setSaveError(
                error instanceof Error
                  ? error.message
                  : "Could not save connector",
              );
            } finally {
              setSaving(false);
            }
          }}
        >
          <FormSection title="Connector">
            <FormField label="Name">
              <InputValue name="name" value={connector?.name} maxLength={200} />
            </FormField>
            <FormField label="Platform">
              <select
                className={styles.select}
                disabled={!!connector}
                value={kind}
                onChange={(e) => setKind(e.target.value as Connector["kind"])}
              >
                <option value="github">GitHub</option>
                <option value="lark">Lark</option>
              </select>
            </FormField>
            <label className={styles.check}>
              <input
                name="enabled"
                type="checkbox"
                defaultChecked={connector?.enabled ?? true}
              />{" "}
              Enabled
            </label>
          </FormSection>
          <FormSection
            title="Intake"
            description={
              kind === "github"
                ? "Use a workspace service account for the GitHub integration."
                : "Connect with a Lark long connection and mount its App secret on Service."
            }
          >
            {kind === "github" ? (
              <>
                <FormField label="Integration service account ID">
                  <InputValue
                    name="principal"
                    value={String(config?.principal_id ?? "")}
                  />
                </FormField>
                <FormField
                  label="Repositories"
                  description="One owner/repository per line."
                >
                  <Textarea
                    name="channels"
                    rows={3}
                    defaultValue={list(config, "channels")}
                    required
                  />
                </FormField>
              </>
            ) : (
              <>
                <FormField label="App ID">
                  <InputValue name="app" value={String(config?.app_id ?? "")} />
                </FormField>
                <FormField label="Bot open ID">
                  <InputValue
                    name="bot"
                    value={String(config?.bot_open_id ?? "")}
                  />
                </FormField>
                <FormField label="Region">
                  <select
                    name="domain"
                    className={styles.select}
                    defaultValue={String(
                      config?.domain ?? "https://open.feishu.cn",
                    )}
                  >
                    <option value="https://open.feishu.cn">Feishu</option>
                    <option value="https://open.larksuite.com">Lark</option>
                  </select>
                </FormField>
                <FormField label="Groups (optional)">
                  <Textarea
                    name="groups"
                    rows={2}
                    defaultValue={list(config, "groups")}
                  />
                </FormField>
                <FormField label="Groups without a mention (optional)">
                  <Textarea
                    name="unmentioned"
                    rows={2}
                    defaultValue={list(config, "unmentioned_groups")}
                  />
                </FormField>
                <label className={styles.check}>
                  <input
                    name="direct"
                    type="checkbox"
                    defaultChecked={config?.direct_messages !== false}
                  />{" "}
                  Accept direct messages
                </label>
              </>
            )}
            <FormField
              label="Allowed senders"
              description="One identifier per line."
            >
              <Textarea
                name="senders"
                rows={3}
                defaultValue={list(config, "senders")}
                required
              />
            </FormField>
          </FormSection>
          <FormSection
            title="Platform credential"
            description={
              kind === "github"
                ? "Use a GitHub token with access to the configured repositories. The intake service account is separate."
                : "Use the App secret from the Lark or Feishu developer console for the selected region."
            }
          >
            <FormField
              label={kind === "github" ? "GitHub token" : "App secret"}
              description={
                connector && credentialState !== "Not configured"
                  ? "Leave blank to keep the saved credential."
                  : "The credential is write-only."
              }
            >
              <Input
                type="password"
                name="credential"
                autoComplete="new-password"
                maxLength={8192}
              />
            </FormField>
            {claw.environment_id ? (
              <label className={styles.check}>
                <input
                  type="checkbox"
                  name="enable-replies"
                  defaultChecked={
                    connector?.reply_environment_id === claw.environment_id
                  }
                />
                Allow replies from this Project Environment
              </label>
            ) : (
              <p className={styles.muted}>
                Attach an Environment before enabling replies.
              </p>
            )}
            <p className={styles.muted}>
              Only enable replies for an Environment you trust. Code running
              there can access the credential during a reply. Reauthorize
              replies after changing the Environment.
            </p>
          </FormSection>
          {connector && (
            <FormSection
              title="Connection status"
              description="Check the saved credential. For GitHub this checks read access to the first configured repository, not write permissions."
            >
              <dl className={styles.facts}>
                <dt>Connector ID</dt>
                <dd>{connector.id}</dd>
                <dt>Credential</dt>
                <dd>{credentialState}</dd>
                {connector.listener_status && (
                  <>
                    <dt>Listener</dt>
                    <dd>
                      <Status state={connector.listener_status} />
                    </dd>
                  </>
                )}
                <dt>Last received</dt>
                <dd>
                  <DateTime value={connector.last_event_at} />
                </dd>
              </dl>
              <div className={styles.actions}>
                <Button
                  type="button"
                  variant="outline"
                  disabled={
                    saving ||
                    check.isPending ||
                    credentialState === "Not configured"
                  }
                  onClick={() =>
                    check.mutate({
                      path: `${path}/check`,
                      method: "POST",
                      body: {},
                    })
                  }
                >
                  {check.isPending ? "Checking…" : "Check credential"}
                </Button>
                {check.data && (
                  <span role="status">
                    <Status state={check.data.state} />
                  </span>
                )}
              </div>
              {check.isError && (
                <p role="alert" className={styles.error}>
                  {check.error.message}
                </p>
              )}
            </FormSection>
          )}
          {saveError && (
            <p role="alert" className={styles.error}>
              {saveError}
            </p>
          )}
          <FormActions pending={saving} onCancel={close} />
        </form>
      )}
    </DetailPage>
  );
}

export function SchedulesPage() {
  return (
    <ClawScope>
      {(claw, picker) => (
        <ScheduleList key={claw.id} claw={claw} picker={picker} />
      )}
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
  const { params, select } = useSelection();
  const { can } = useAccess();
  const id = params.get("schedule");
  const fresh = params.get("owner") === "service";
  const [pages, setPages] = React.useState<string[]>([]);
  const base = clawPath(claw.id);
  const query = useRead<Collection<Schedule | FreshSchedule>>(
    id && id !== "new"
      ? null
      : url(`${base}/${fresh ? "fresh-schedules" : "schedules"}`, {
          cursor: pages.at(-1),
        }),
  );
  const detail = useRead<Schedule>(
    id && id !== "new" ? `${base}/schedules/${encodeURIComponent(id)}` : null,
  );
  const close = () => select({ schedule: null });
  if (id === "new" && can("write"))
    return <ScheduleEditor claw={claw} close={close} />;
  if (id && id !== "new") {
    if (detail.isPending) return <Loading page />;
    if (detail.isError) return <ErrorPage error={detail.error} />;
    return (
      <ScheduleDetails
        key={id}
        claw={claw}
        schedule={detail.data}
        close={close}
      />
    );
  }
  const rows = (query.data?.items ?? []).map((entry) => ({
    ...entry,
    state:
      "state" in entry ? entry.state : entry.enabled ? "enabled" : "paused",
    owner: fresh ? "Service" : "Main",
  }));
  const add = can("write") ? (
    <Button
      variant={rows.length ? "outline" : "default"}
      onClick={() => select({ schedule: "new" })}
    >
      <Icon name="plus" /> New schedule
    </Button>
  ) : undefined;
  return (
    <Page
      title="Schedules"
      description="Deliver to Main or start a fresh Thread on each occurrence."
      titleAction={
        <div className={styles.actions}>
          {picker}
          {!!rows.length && add}
        </div>
      }
    >
      <select
        aria-label="Schedule owner"
        className={styles.select}
        value={fresh ? "service" : "main"}
        onChange={(event) => {
          setPages([]);
          select({
            owner: event.currentTarget.value === "service" ? "service" : null,
            schedule: null,
          });
        }}
      >
        <option value="main">Main</option>
        <option value="service">Fresh Threads</option>
      </select>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorPage error={query.error} />
      ) : rows.length ? (
        <ResourceTable
          caption="Schedules"
          items={rows}
          columns={[
            {
              label: "Schedule",
              tone: "primary",
              render: (row) =>
                row.owner === "Service" ? (
                  <ResourceLink path={`schedules/${row.id}`}>
                    {row.name}
                  </ResourceLink>
                ) : (
                  <button
                    className={styles.rowAction}
                    onClick={() => select({ schedule: row.id })}
                  >
                    {row.name}
                    <Icon name="next" />
                  </button>
                ),
            },
            { label: "Owner", render: (row) => row.owner },
            { label: "State", render: (row) => <Status state={row.state} /> },
            {
              label: "Next delivery",
              render: (row) => <DateTime value={row.next_at} />,
            },
          ]}
        />
      ) : (
        <Empty
          title="No schedules"
          description="Create a schedule to automate recurring work."
          action={add}
        />
      )}
      {query.data && (pages.length > 0 || query.data.next_cursor) && (
        <Pager
          next={!!query.data.next_cursor}
          onPrevious={
            pages.length ? () => setPages(pages.slice(0, -1)) : undefined
          }
          onNext={() => {
            const cursor = query.data.next_cursor;
            if (cursor) setPages([...pages, cursor]);
          }}
        />
      )}
    </Page>
  );
}
function ScheduleEditor({ claw, close }: { claw: Claw; close: () => void }) {
  const [mode, setMode] = React.useState("cron");
  const save = useWrite<Schedule | FreshSchedule>();
  const request = React.useRef(crypto.randomUUID());
  return (
    <Page title="New schedule">
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          const rule =
            mode === "cron"
              ? {
                  type: mode,
                  expression: field(form, "cron"),
                  timezone: field(form, "zone"),
                }
              : {
                  type: mode,
                  run_at: new Date(field(form, "time")).toISOString(),
                };
          const target =
            field(form, "target") === "fresh" ? "fresh-schedules" : "schedules";
          save.mutate(
            {
              path: `${clawPath(claw.id)}/${target}`,
              method: "POST",
              body: {
                name: field(form, "name"),
                prompt: field(form, "prompt"),
                rule,
              },
              key: request.current,
            },
            { onSuccess: close },
          );
        }}
      >
        <FormSection title="Task">
          <FormField label="Name">
            <InputValue name="name" maxLength={128} />
          </FormField>
          <FormField label="Prompt">
            <Textarea name="prompt" rows={5} required maxLength={32000} />
          </FormField>
          <FormField label="Target">
            <select className={styles.select} name="target">
              <option value="main">Main Inbox</option>
              <option value="fresh" disabled={!claw.environment_id}>
                Fresh Thread
              </option>
            </select>
          </FormField>
        </FormSection>
        <FormSection title="Timing">
          <FormField label="Frequency">
            <select
              className={styles.select}
              value={mode}
              onChange={(e) => setMode(e.target.value)}
            >
              <option value="cron">Recurring</option>
              <option value="once">Once</option>
            </select>
          </FormField>
          {mode === "cron" ? (
            <>
              <FormField label="Cron expression">
                <InputValue name="cron" />
              </FormField>
              <FormField label="Time zone">
                <InputValue name="zone" value="UTC" />
              </FormField>
            </>
          ) : (
            <FormField label="Run at (local time)">
              <Input type="datetime-local" name="time" required />
            </FormField>
          )}
        </FormSection>
        {save.isError && (
          <p role="alert" className={styles.error}>
            {save.error.message}
          </p>
        )}
        <FormActions pending={save.isPending} onCancel={close} />
      </form>
    </Page>
  );
}
function ScheduleDetails({
  claw,
  schedule,
  close,
}: {
  claw: Claw;
  schedule: Schedule;
  close: () => void;
}) {
  const path = `${clawPath(claw.id)}/schedules/${schedule.id}`;
  const { can } = useAccess();
  const [pages, setPages] = React.useState<string[]>([]);
  const rows = useRead<Collection<Occurrence>>(
    url(`${path}/occurrences`, { cursor: pages.at(-1) }),
    pages.length ? undefined : 10000,
  );
  const save = useWrite<Schedule>();
  const enabled = save.data?.enabled ?? schedule.enabled;
  return (
    <Page
      title={schedule.name}
      description={
        schedule.cron
          ? `${schedule.cron} · ${schedule.timezone}`
          : "One-time schedule"
      }
      titleAction={
        <div className={styles.actions}>
          <Button variant="ghost" onClick={close}>
            Schedules
          </Button>
          {can("write") && (
            <Button
              variant="outline"
              disabled={
                save.isPending ||
                (!enabled && schedule.next_at === null && !schedule.cron)
              }
              onClick={() =>
                save.mutate({
                  path,
                  method: "PATCH",
                  body: { enabled: !enabled },
                })
              }
            >
              {enabled ? "Pause" : "Enable"}
            </Button>
          )}
        </div>
      }
    >
      <div className={styles.actions}>
        <Status state={enabled ? "enabled" : "paused"} />
        <DateTime value={schedule.next_at} />
      </div>
      <pre className={styles.payload}>{schedule.prompt}</pre>
      {save.isError && (
        <p role="alert" className={styles.error}>
          {save.error.message}
        </p>
      )}
      {rows.isPending ? (
        <Loading />
      ) : rows.isError ? (
        <ErrorPage error={rows.error} />
      ) : rows.data.items.length ? (
        <ResourceTable
          caption="Occurrences"
          items={rows.data.items}
          columns={[
            {
              label: "Occurrence",
              tone: "primary",
              render: (row) => <DateTime value={row.scheduled_at} />,
            },
            {
              label: "Outcome",
              render: (row) => <Status state={row.status} />,
            },
            {
              label: "Main Run",
              render: (row) =>
                row.run_id ? (
                  <ThreadLink
                    session={claw.main_session_id}
                    thread={claw.main_thread_id}
                    run={row.run_id}
                  >
                    Inspect
                  </ThreadLink>
                ) : (
                  "—"
                ),
            },
          ]}
        />
      ) : (
        <Empty
          title="No occurrences"
          description="The first delivery will appear here."
        />
      )}
      {rows.data && (pages.length > 0 || rows.data.next_cursor) && (
        <Pager
          next={!!rows.data.next_cursor}
          onPrevious={
            pages.length ? () => setPages(pages.slice(0, -1)) : undefined
          }
          onNext={() => {
            const cursor = rows.data.next_cursor;
            if (cursor) setPages([...pages, cursor]);
          }}
        />
      )}
    </Page>
  );
}
