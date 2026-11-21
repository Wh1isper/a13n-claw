import {
  useApp,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
  useMutation,
  useSearchParams,
} from "@a13n/console";

export interface Claw {
  id: string;
  name: string;
  workspace_id: string;
  agent_id: string;
  agent_revision_id: string | null;
  environment_id: string | null;
  browser_id: string | null;
  service_account_id: string;
  memory_space_id: string;
  main_thread_id: string | null;
  main_session_id: string | null;
  heartbeat_seconds: number;
  unread_limit: number;
  read_limit: number;
  worker_models: { model: string; description: string }[];
}
export interface Admission {
  status: "accepted" | "duplicate";
  item_id: string;
  delivery_id: string | null;
}
export interface ResourceChoice {
  id: string;
  name: string;
}
export type ResourceKind =
  "agent" | "account" | "environment" | "template" | "browser" | "model";
export interface Activity {
  current_run_id: string | null;
  last_run_id: string | null;
  last_status: string | null;
}
export interface Overview {
  claw: Claw;
  unread: number;
  read: number;
  main: Activity | null;
  last_failure: { code: string; message: string } | null;
  agent_revision_outdated: boolean;
  project: {
    environment: string;
    environment_name: string | null;
    environment_failure: {
      code: string;
      message: string;
      permanent: boolean;
    } | null;
    browser_failure: string | null;
    fresh_schedule_count: number;
    unauthorized_reply_connectors: number;
    browser_name: string | null;
    browser: string | null;
    automation_account: string;
    cli_versions: Record<string, string> | null;
    cli_update_state: "queued" | "succeeded" | "failed" | null;
    cli_update_error: string | null;
  };
}
export interface Worker {
  thread_id: string;
  session_id: string;
  name: string;
  model: string | null;
  activity: Activity | null;
}
export interface Delivery {
  id: string;
  item_id: string | null;
  kind: string | null;
  preview: string | null;
  read_at: string | null;
  origin: string;
  status: string;
  thread_id: string | null;
  session_id: string | null;
  run_id: string | null;
  created_at: string;
}
export interface DeliveryDetail extends Delivery {
  content: string | null;
  source: Record<string, unknown> | null;
}
export interface Connector {
  id: string;
  name: string;
  kind: "github" | "lark";
  enabled: boolean;
  last_event_at: string | null;
  credential_configured: boolean;
  listener_status: string | null;
  reply_environment_id: string | null;
  reply_reauthorization_required: boolean;
  config: Record<string, unknown>;
}
export interface Schedule {
  id: string;
  name: string;
  prompt: string;
  cron: string | null;
  deleted_at: string | null;
  lateness_seconds: number;
  timezone: string;
  enabled: boolean;
  next_at: string | null;
}
export interface FreshSchedule {
  id: string;
  name: string;
  state: string;
  next_at: string | null;
}
export interface Occurrence {
  id: string;
  scheduled_at: string;
  delivery_id: string | null;
  status: string;
  item_id: string | null;
  run_id: string | null;
}
export interface Collection<T> {
  items: T[];
  next_cursor: string | null;
}

export function url(
  path: string,
  params: Record<string, string | null | undefined> = {},
) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params))
    if (value) query.set(key, value);
  return query.size ? `${path}?${query}` : path;
}
export const clawPath = (id: string) => `/claws/${encodeURIComponent(id)}`;
export const appPath = (workspace: string, page: string) =>
  page === "settings"
    ? `/workspace/${workspace}/settings/app.a13n.claw.settings`
    : `/workspace/${workspace}/apps/a13n.claw/${page}`;
export const threadPath = (
  workspace: string,
  session: string,
  thread: string,
  run?: string | null,
) =>
  `/workspace/${workspace}/sessions/${session}/threads/${thread}${run ? `/runs/${run}` : ""}`;

export async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(
      body?.error?.message ?? `Request failed (${response.status})`,
    );
  }
  return response.status === 204
    ? (undefined as T)
    : (response.json() as Promise<T>);
}
export function useRead<T>(path: string | null, interval?: number) {
  const app = useApp();
  return useQuery({
    queryKey: app.queryKey(path ?? "disabled"),
    enabled: path !== null,
    queryFn: async ({ signal }) =>
      json<T>(await app.request(path!, { signal })),
    refetchInterval: interval,
  });
}
export function useProjectResources(
  kind: ResourceKind,
  enabled: boolean,
  account?: string,
) {
  const app = useApp();
  const path = `/project-resources/${kind}`;
  return useInfiniteQuery({
    queryKey: app.queryKey(path, account ?? ""),
    enabled,
    initialPageParam: null as string | null,
    queryFn: async ({ signal, pageParam }) =>
      json<Collection<ResourceChoice>>(
        await app.request(
          url(path, { cursor: pageParam, account_id: account }),
          { signal },
        ),
      ),
    getNextPageParam: (page) => page.next_cursor,
  });
}
export function useWrite<T>() {
  const app = useApp();
  const cache = useQueryClient();
  return useMutation({
    mutationFn: async ({
      path,
      method,
      body,
      key,
    }: {
      path: string;
      method: string;
      body: unknown;
      key?: string;
    }) =>
      json<T>(
        await app.request(path, {
          method,
          body: JSON.stringify(body),
          headers: {
            "Content-Type": "application/json",
            ...(key ? { "Idempotency-Key": key } : {}),
          },
        }),
      ),
    onSuccess: () => cache.invalidateQueries({ queryKey: app.queryKey() }),
  });
}
export function useSelection() {
  const [params, setParams] = useSearchParams();
  const select = (values: Record<string, string | null>) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(values)) {
        if (value === null) next.delete(key);
        else next.set(key, value);
      }
      return next;
    });
  return { params, select };
}
