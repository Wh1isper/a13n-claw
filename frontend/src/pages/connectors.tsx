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
  useNavigate,
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
import { CopyButton } from "../ui/copy-button";

import { field, items, list, ResourcePicker } from "./form-common";

interface CheckResult {
  state: string;
  reason?: string;
  http_status?: number;
}

function EndpointControl({
  value,
  id,
  disabled,
  ...accessibility
}: {
  value: string;
  id?: string;
  disabled?: boolean;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
}) {
  return (
    <div className={styles.endpoint}>
      <Input
        id={id}
        disabled={disabled}
        {...accessibility}
        readOnly
        value={value}
      />
      <CopyButton value={value} />
    </div>
  );
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
      description={copy(
        "Control platform intake and credentials.",
        "管理平台接收和凭据。",
      )}
      actions={
        <div className={`${styles.actions} ${styles.headerActions}`}>
          {picker}
          {can("admin") && !!query.data?.items.length && add}
        </div>
      }
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
            onRowActivate={(row) => select({ connector: row.id })}
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
                    <Icon name="plug" />
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
  const [githubMode, setGithubMode] = React.useState(
    String(config?.mode ?? "external"),
  );
  const [markRead, setMarkRead] = React.useState(config?.mark_read === true);
  const [account, setAccount] = React.useState(
    String(config?.principal_id ?? ""),
  );
  const [domain, setDomain] = React.useState(
    String(config?.domain ?? "https://open.feishu.cn"),
  );
  const [direct, setDirect] = React.useState(config?.direct_messages !== false);
  const [enabled, setEnabled] = React.useState(row?.enabled ?? true);
  const [egressPrimary, setEgressPrimary] = React.useState(
    row?.egress_primary ?? false,
  );
  const [egressEnvironmentId] = React.useState(claw.environment_id);
  const [dirty, setDirty] = React.useState(false);
  const formRef = React.useRef<HTMLFormElement>(null);
  const discard = () => {
    formRef.current?.reset();
    setKind(row?.kind ?? "github");
    setGithubMode(String(config?.mode ?? "external"));
    setMarkRead(config?.mark_read === true);
    setAccount(String(config?.principal_id ?? ""));
    setDomain(String(config?.domain ?? "https://open.feishu.cn"));
    setDirect(config?.direct_messages !== false);
    setEnabled(row?.enabled ?? true);
    setEgressPrimary(row?.egress_primary ?? false);
    setDirty(false);
    write.reset();
  };
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
        {row.listener_status && (
          <RailRow label={copy("Listener", "监听状态")}>
            <Status state={row.listener_status} />
          </RailRow>
        )}
        {row.egress_status && (
          <RailRow label={copy("Session credential", "会话凭据")}>
            <Status state={row.egress_status} />
          </RailRow>
        )}
        {row.egress_failure && (
          <p role="alert">
            {copy(
              "Session credential synchronization failed. Claw will retry; rotate or revoke the credential at the provider if access must end immediately.",
              "会话凭据同步失败，Claw 将重试。如需立即终止访问，请在平台侧轮换或撤销凭据。",
            )}
          </p>
        )}
        <RailRow label={copy("Last received", "最近接收")}>
          <DateTime value={row.last_event_at} />
        </RailRow>
        {allowed && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={check.isPending}
            onClick={() =>
              check.mutate({ path: `${path}/check`, method: "POST", body: {} })
            }
          >
            {copy("Check saved connection", "检查已保存的连接")}
          </Button>
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
      </RailSection>
      {allowed && (
        <RailSection title={copy("Credential & danger zone", "凭据与危险操作")}>
          <div className={styles.dangerActions}>
            {row.credential_configured && (
              <Confirm
                title={copy("Remove saved credential?", "移除已保存的凭据？")}
                subject={row.name}
                danger
                triggerVariant="outline"
                description={copy(
                  "Claw queues removal of the Session credential. Rotate or revoke the credential at the provider if access must end immediately.",
                  "Claw 将排队移除会话凭据。如需立即终止访问，请在平台侧轮换或撤销凭据。",
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
                      egress_primary: row.egress_primary,
                      egress_environment_id: row.egress_primary
                        ? egressEnvironmentId
                        : undefined,
                      config: row.config,
                      credential: null,
                    },
                  })
                }
                onSuccess={() => {
                  check.reset();
                  toast.add({
                    title: copy("Saved credential removed", "已移除保存的凭据"),
                  });
                }}
              />
            )}
            <Confirm
              title={copy("Delete this connector?", "删除此连接器？")}
              subject={row.name}
              danger
              triggerVariant="outline"
              description={copy(
                "Intake stops and Session credential removal is queued. Existing deliveries remain available. Rotate or revoke the credential at the provider if access must end immediately.",
                "停止接收并排队移除会话凭据，已有投递记录保留。如需立即终止访问，请在平台侧轮换或撤销凭据。",
              )}
              trigger={copy("Delete connector", "删除连接器")}
              action={() =>
                write.mutateAsync({ path, method: "DELETE", body: null })
              }
              onSuccess={close}
            />
          </div>
        </RailSection>
      )}
    </Rail>
  );
  return (
    <DetailPage
      back={back}
      backLabel={copy("Connectors", "连接器")}
      header={
        <DetailHeader
          avatar={<Icon name="plug" />}
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
          ref={formRef}
          onChange={() => setDirty(true)}
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const body = {
              name: field(form, "name"),
              kind,
              enabled,
              egress_primary: egressPrimary,
              egress_environment_id: egressPrimary
                ? egressEnvironmentId
                : undefined,
              config:
                kind === "github"
                  ? {
                      mode: githubMode,
                      principal_id: account || null,
                      channels: items(form, "channels"),
                      senders: items(form, "senders"),
                      api_url:
                        field(form, "api_url") || "https://api.github.com",
                      poll_interval_seconds: Number(
                        field(form, "poll_interval") || 60,
                      ),
                      initial_lookback_seconds: Number(
                        field(form, "lookback") || 3600,
                      ),
                      max_source_notification_delay_seconds: Number(
                        field(form, "max_delay") || 86400,
                      ),
                      mark_read: markRead,
                    }
                  : {
                      app_id: field(form, "app"),
                      bot_open_id: field(form, "bot"),
                      domain,
                      groups: items(form, "groups"),
                      unmentioned_groups: items(form, "unmentioned"),
                      direct_messages: direct,
                      senders: items(form, "senders"),
                      event_types: items(form, "event_types"),
                    },
              credential: field(form, "credential") || undefined,
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
            <FormField
              data-field-layout="setting"
              label={copy("Enabled", "启用")}
            >
              <Switch
                checked={enabled}
                onCheckedChange={(value) => {
                  setEnabled(value);
                  setDirty(true);
                }}
                disabled={!allowed}
              />
            </FormField>
          </FormSection>
          <FormSection
            title={copy("Intake", "消息接收")}
            description={
              kind === "github"
                ? githubMode === "polling"
                  ? copy(
                      "Poll GitHub Notifications with a classic token that has notifications and repo access. An empty repository list includes all visible repositories.",
                      "使用具有 notifications 和 repo 权限的经典令牌轮询 GitHub 通知。仓库列表留空时包含所有可见仓库。",
                    )
                  : copy(
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
                <ChoiceField
                  label={copy("GitHub intake mode", "GitHub 接收模式")}
                  disabled={!allowed}
                  value={githubMode}
                  onValueChange={(value) => {
                    setGithubMode(value);
                    setDirty(true);
                  }}
                  options={[
                    {
                      value: "external",
                      label: copy("External events", "外部事件"),
                    },
                    {
                      value: "polling",
                      label: copy("Notifications polling", "通知轮询"),
                    },
                  ]}
                />
                {githubMode === "external" && (
                  <ResourcePicker
                    label={copy("Integration service account", "集成服务账号")}
                    kind="account"
                    value={account}
                    onChange={(value) => {
                      setAccount(value);
                      setDirty(true);
                    }}
                    disabled={!allowed}
                  />
                )}
                {row && githubMode === "external" && (
                  <FormField
                    data-field-layout="stacked"
                    label={copy("Intake endpoint", "接收端点")}
                  >
                    <EndpointControl value={endpoint} />
                  </FormField>
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
                    required={githubMode === "external"}
                    readOnly={!allowed}
                    defaultValue={list(config, "channels")}
                  />
                </FormField>
                {githubMode === "polling" && (
                  <>
                    <FormField
                      label={copy("GitHub API URL", "GitHub API 地址")}
                    >
                      <Input
                        name="api_url"
                        type="url"
                        defaultValue={String(
                          config?.api_url ?? "https://api.github.com",
                        )}
                        readOnly={!allowed}
                        required
                      />
                    </FormField>
                    <FormField
                      label={copy("Poll interval (seconds)", "轮询间隔（秒）")}
                    >
                      <Input
                        name="poll_interval"
                        type="number"
                        min={15}
                        max={86400}
                        defaultValue={String(
                          config?.poll_interval_seconds ?? 60,
                        )}
                        readOnly={!allowed}
                        required
                      />
                    </FormField>
                    <FormField
                      label={copy(
                        "Initial lookback (seconds)",
                        "首次回溯（秒）",
                      )}
                    >
                      <Input
                        name="lookback"
                        type="number"
                        min={0}
                        max={2592000}
                        defaultValue={String(
                          config?.initial_lookback_seconds ?? 3600,
                        )}
                        readOnly={!allowed}
                        required
                      />
                    </FormField>
                    <FormField
                      label={copy(
                        "Maximum new-subject delay (seconds)",
                        "新主题最长延迟（秒）",
                      )}
                    >
                      <Input
                        name="max_delay"
                        type="number"
                        min={0}
                        max={2592000}
                        defaultValue={String(
                          config?.max_source_notification_delay_seconds ??
                            86400,
                        )}
                        readOnly={!allowed}
                        required
                      />
                    </FormField>
                    <FormField
                      label={copy(
                        "Mark accepted notifications read",
                        "将已接收通知标记为已读",
                      )}
                    >
                      <Checkbox
                        checked={markRead}
                        disabled={!allowed}
                        onCheckedChange={(value) => {
                          setMarkRead(value);
                          setDirty(true);
                        }}
                      />
                    </FormField>
                  </>
                )}
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
                  onValueChange={(value) => {
                    setDomain(value);
                    setDirty(true);
                  }}
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
                    onCheckedChange={(value) => {
                      setDirect(value);
                      setDirty(true);
                    }}
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
                  label={copy("Event types", "事件类型")}
                  description={copy(
                    "Subscribe to these events in the Lark developer console as well. Separate entries with spaces or new lines.",
                    "同时在飞书开发者控制台订阅这些事件。用空格或换行分隔。",
                  )}
                >
                  <Textarea
                    name="event_types"
                    rows={4}
                    readOnly={!allowed}
                    defaultValue={
                      config?.event_types
                        ? list(config, "event_types")
                        : [
                            "im.message.receive_v1",
                            "im.chat.member.bot.added_v1",
                            "im.chat.member.user.added_v1",
                            "drive.notice.comment_add_v1",
                          ].join("\n")
                    }
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
              description={
                kind === "github" && githubMode === "polling"
                  ? copy(
                      "Separate usernames with spaces or new lines; * allows any sender except the bot itself.",
                      "用空格或换行分隔用户名；* 允许机器人自身以外的所有发送人。",
                    )
                  : copy(
                      "Separate entries with spaces or new lines.",
                      "用空格或换行分隔多个条目。",
                    )
              }
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
          <FormSection title={copy("Credentials", "凭据")}>
            <FormField
              data-field-layout="setting"
              label={copy("Use for Project egress", "用于项目网络凭据")}
              description={copy(
                "Select one connector per platform. Unconfigured Threads use controlled public egress; explicit policies are preserved.",
                "每个平台选择一个连接器。未配置的线程使用受控公共网络策略；已有策略保持不变。",
              )}
            >
              <Switch
                checked={egressPrimary}
                onCheckedChange={(value) => {
                  setEgressPrimary(value);
                  setDirty(true);
                }}
                disabled={!allowed || !egressEnvironmentId}
              />
            </FormField>
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
            <p>
              {copy(
                "Main can use authorized platform CLIs to reply; Fresh Scheduled Threads report to Main.",
                "Main 可使用已授权的平台 CLI 回复；新的计划线程向 Main 报告结果。",
              )}
            </p>
          </FormSection>
          {write.isError && <ErrorNotice error={write.error} />}
          {allowed &&
            (row ? (
              dirty && (
                <SaveBar
                  title={copy("Unsaved changes", "有未保存的更改")}
                  consequence={copy(
                    "Save connector settings",
                    "保存连接器设置",
                  )}
                  saveLabel={copy("Save connector", "保存连接器")}
                  discardLabel={copy("Discard", "放弃更改")}
                  pending={write.isPending}
                  disabled={
                    kind === "github" && githubMode === "external" && !account
                  }
                  onDiscard={discard}
                />
              )
            ) : (
              <FormActions
                pending={write.isPending}
                disabled={
                  kind === "github" && githubMode === "external" && !account
                }
                onCancel={close}
                label={copy("Save connector", "保存连接器")}
              />
            ))}
        </form>
      </DetailLayout>
    </DetailPage>
  );
}
