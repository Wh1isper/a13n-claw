import { isMap, parseDocument } from "yaml";

export type Fields = Record<string, unknown>;
export type ConfigurationStatus = {
  path: string;
  text: string;
  revision: string;
  active_revision: string | null;
  pending: boolean;
  document: Fields | null;
  issues: Issue[];
  restart_required: string[];
};
export type Issue = { path: string; code: string; message: string };
export type Preview = {
  valid: boolean;
  issues: Issue[];
  changes: { resource: string; action: string }[];
};
export const sections = [
  {
    key: "models",
    label: "Models",
    detail: "Providers and credential references",
  },
  {
    key: "environments",
    label: "Environments",
    detail: "Workspace access and execution",
  },
  {
    key: "profiles",
    label: "Profiles",
    detail: "Agent behavior and permissions",
  },
  { key: "skills", label: "Skills", detail: "Reusable instructions and files" },
  {
    key: "mcp",
    label: "MCP servers",
    detail: "Remote tools and allowed names",
  },
  {
    key: "channels",
    label: "Channels",
    detail: "Ingress, routing and delivery",
  },
  {
    key: "defaults",
    label: "Defaults",
    detail: "Starting profile for new work",
  },
  {
    key: "server",
    label: "Server",
    detail: "Launch settings · restart required",
  },
];
export const templates: Record<string, Fields> = {
  models: {
    provider: "openai",
    model_name: "",
    credential_env: "OPENAI_API_KEY",
  },
  environments: { kind: "local", files: "read", shell: false },
  profiles: {
    model_id: "",
    environment_id: "",
    agent: {
      instructions: "You are a helpful assistant.",
      usage_limits: { request_limit: 50 },
    },
  },
  skills: { name: "new-skill", description: "", instructions: "" },
  mcp: { url: "" },
  channels: {
    platform: "http",
    account: "default",
    channel: "",
    ingress_actor_id: "",
    allowed_senders: [],
    enabled: false,
  },
};
export function fields(value: unknown): Fields {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Fields)
    : {};
}
export function readSource(source: string) {
  try {
    // Match the backend's YAML 1.1 scalar interpretation, including yes/no values.
    const document = parseDocument(source, { version: "1.1" });
    if (document.errors.length || !isMap(document.contents)) return null;
    const value: unknown = document.toJS({ maxAliasCount: 100 });
    return { document, value: fields(value) };
  } catch {
    return null;
  }
}
export function patchSource(source: string, path: string[], value: unknown) {
  const parsed = readSource(source);
  if (!parsed) throw new Error("Fix the YAML syntax before editing fields.");
  if (value === undefined) parsed.document.deleteIn(path);
  else {
    // setIn creates missing parents as JS Maps, serialized as !!omap in YAML 1.1.
    // Configuration groups are ordinary mappings on both sides of the API.
    for (let length = 1; length < path.length; length++) {
      const parent = path.slice(0, length);
      if (!parsed.document.hasIn(parent))
        parsed.document.setIn(parent, parsed.document.createNode({}));
    }
    parsed.document.setIn(path, value);
  }
  return parsed.document.toString();
}
export const resourceKind: Record<string, string> = {
  models: "model",
  environments: "environment",
  profiles: "profile",
  skills: "skill",
  mcp: "mcp",
  defaults: "defaults",
};
export function starter(source: string) {
  let result = source;
  result = patchSource(result, ["models", "primary"], {
    ...templates.models,
    model_name: "replace-with-your-model",
  });
  result = patchSource(
    result,
    ["environments", "local"],
    templates.environments,
  );
  result = patchSource(result, ["profiles", "default"], {
    ...templates.profiles,
    model_id: "primary",
    environment_id: "local",
  });
  return patchSource(result, ["defaults", "profile_id"], "default");
}
