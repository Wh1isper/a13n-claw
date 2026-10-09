import { useEffect, useState } from "react";

export type Principal = {
  id: string;
  version: number;
  admin: boolean;
  enabled: boolean;
  thread_ids: string[];
  profile_ids: string[];
  actions: string[];
};
export type Instance = {
  version: string;
  principal: Principal;
  dispatcher: string;
  error: string | null;
  workspace: string | null;
  connectivity: string;
};
export type Resource = {
  id: string;
  kind: string;
  version: number;
  content: Record<string, unknown>;
  retired: boolean;
};
export type Thread = {
  id: string;
  title: string;
  profile_id: string;
  version: number;
  checkpoint_id: string | null;
  parent_thread_id: string | null;
  parent_run_id: string | null;
  archived: boolean;
  created_at: string;
};
export type Run = {
  id: string;
  thread_id: string;
  status: string;
  composition: {
    profile: Resource;
    model: Resource;
    environment: Resource;
    workspace: string;
  };
  checkpoint_id: string | null;
  recovery_of: string | null;
  recovery_required: boolean;
  reconciled_at: string | null;
  cancel_requested: boolean;
  output: string | null;
  error: string | null;
  created_at: string;
};
export type Input = {
  id: string;
  run_id: string;
  actor_id: string;
  request_id: string;
  text: string;
  source: string;
  disposition: string;
  attachment_ids: string[];
};
export type Asset = {
  id: string;
  thread_id: string;
  kind: string;
  name: string;
  size: number;
  sha256: string;
};
export type Target = {
  id: string;
  status: string;
  generation: string;
  definition: { kind: string; configuration: unknown };
  error: string | null;
};
export type Call = { tool_call_id: string; tool_name: string; args: unknown };
export type Decision = {
  id: string;
  checkpoint_id: string;
  requests: { approvals: Call[]; calls: Call[] };
  response: unknown | null;
};
export type Child = {
  id: string;
  child_thread_id: string;
  status: string;
  output: string | null;
  error: string | null;
  cancel_policy: string;
  notify: boolean;
  delivery_input_id: string | null;
  delivery_error: string | null;
};
export type Submission = {
  request_id: string;
  text: string;
  attachment_ids: string[];
  separate_run: boolean;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
export class Api {
  constructor(private token: string) {}
  async raw(path: string, init: RequestInit = {}): Promise<Response> {
    let response: Response;
    try {
      response = await fetch(`/api${path}`, {
        ...init,
        cache: "no-store",
        headers: { ...init.headers, Authorization: `Bearer ${this.token}` },
      });
    } catch {
      throw new ApiError(
        0,
        "connection_lost",
        "Connection lost. A mutation may have been accepted; reconcile saved state before retrying.",
      );
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new ApiError(
        response.status,
        body.error?.code ?? "request_failed",
        body.error?.message ?? `Request failed (${response.status})`,
      );
    }
    return response;
  }
  async get<T>(path: string): Promise<T> {
    return (await this.raw(path)).json();
  }
  async send<T>(path: string, body?: unknown, method = "POST"): Promise<T> {
    const response = await this.raw(path, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return response.status === 204 ? (undefined as T) : response.json();
  }
  async download(asset: Asset) {
    const response = await this.raw(`/files/${asset.id}/download`);
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = asset.name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

export function useRemote<T>(api: Api, path: string | null, poll = 0) {
  const [record, setRecord] = useState<{ path: string; data: T }>();
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setError("");
    if (!path) return;
    const load = async () => {
      try {
        const data = await api.get<T>(path);
        if (active) {
          setRecord({ path, data });
          setError("");
        }
      } catch (reason) {
        if (active) setError(message(reason));
      }
      if (active && poll) timer = setTimeout(load, poll);
    };
    void load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [api, path, revision, poll]);
  return {
    data: record?.path === path ? record.data : undefined,
    error,
    reload: () => setRevision((value) => value + 1),
  };
}
export function message(error: unknown) {
  return error instanceof Error ? error.message : "Request could not complete";
}
export function pretty(value: unknown) {
  return JSON.stringify(value, null, 2);
}
export function requestId() {
  return crypto.randomUUID();
}
export const activeStatuses = new Set([
  "queued",
  "preparing",
  "running",
  "waiting",
]);
export function allowed(actor: Principal, action: string) {
  return actor.admin || actor.actions.includes(action);
}
