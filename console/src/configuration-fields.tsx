import { useState, type ReactNode } from "react";
import { fields, resourceKind, type Fields } from "./configuration-document";

type Props = {
  section: string;
  id: string;
  value: Fields;
  catalog: Fields;
  update: (path: string[], value: unknown) => void;
  patch: (path: string[], value: unknown) => void;
};

export function ConfigurationFields({
  section,
  id,
  value,
  catalog,
  update,
  patch,
}: Props) {
  const text = (
    label: string,
    key: string,
    hint?: string,
    multiline = false,
  ) => (
    <label>
      {label}
      {multiline ? (
        <textarea
          rows={6}
          value={String(value[key] ?? "")}
          onChange={(e) => update([key], e.target.value)}
        />
      ) : (
        <input
          value={String(value[key] ?? "")}
          onChange={(e) => update([key], e.target.value || undefined)}
        />
      )}
      {hint && <small>{hint}</small>}
    </label>
  );
  const choice = (
    label: string,
    path: string[],
    options: string[],
    current: unknown,
    fallback = "",
  ) => {
    const selected = String(current ?? fallback);
    return (
      <label>
        {label}
        <select
          value={selected}
          onChange={(e) => update(path, e.target.value || undefined)}
        >
          <option value="">Not set</option>
          {selected && !options.includes(selected) && (
            <option value={selected}>{selected} · unavailable</option>
          )}
          {options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>
    );
  };
  const toggle = (
    label: string,
    key: string,
    fallback = false,
    hint?: string,
  ) => (
    <div>
      <label className="check-label">
        <input
          type="checkbox"
          checked={(value[key] ?? fallback) === true}
          onChange={(e) => update([key], e.target.checked)}
        />
        {label}
      </label>
      {hint && <p className="subtle">{hint}</p>}
    </div>
  );
  const number = (
    label: string,
    path: string[],
    current: unknown,
    hint: string,
    minimum = 0,
    step: number | "any" = 1,
  ) => (
    <label>
      {label}
      <input
        type="number"
        min={minimum}
        step={step}
        value={current == null ? "" : String(current)}
        onChange={(e) =>
          update(
            path,
            e.target.value === "" ? undefined : Number(e.target.value),
          )
        }
      />
      <small>{hint}</small>
    </label>
  );
  const ids = (group: string) =>
    Object.keys(fields(catalog[group])).filter(
      (key) =>
        !Array.isArray(catalog.retired) ||
        !catalog.retired.includes(`${resourceKind[group]}/${key}`),
    );
  const references = (label: string, key: string, group: string) => {
    const selected = Array.isArray(value[key]) ? (value[key] as string[]) : [];
    const options = [...new Set([...ids(group), ...selected])];
    return (
      <fieldset className="config-choices">
        <legend>{label}</legend>
        {options.length ? (
          options.map((option) => (
            <label className="check-label" key={option}>
              <input
                type="checkbox"
                checked={selected.includes(option)}
                onChange={(e) =>
                  update(
                    [key],
                    e.target.checked
                      ? [...selected, option]
                      : selected.filter((item) => item !== option),
                  )
                }
              />
              {option}
              {!ids(group).includes(option) && <small> unavailable</small>}
            </label>
          ))
        ) : (
          <p className="subtle">
            Create a{" "}
            {group === "profiles"
              ? "profile"
              : group === "mcp"
                ? "MCP server"
                : "skill"}{" "}
            first.
          </p>
        )}
      </fieldset>
    );
  };
  const agent = fields(value.agent);
  const limits = fields(agent.usage_limits);
  const settings = fields(agent.model_settings);
  const permissions = fields(value.permissions);
  const retiredKey = `${resourceKind[section]}/${id || "instance"}`;
  const retired = Array.isArray(catalog.retired)
    ? (catalog.retired as string[])
    : [];
  let content: ReactNode;
  switch (section) {
    case "models":
      content = (
        <>
          <div className="config-columns">
            {choice(
              "Provider",
              ["provider"],
              ["openai", "anthropic", "google"],
              value.provider,
            )}
            {text(
              "Model name",
              "model_name",
              "Use the provider's exact model identifier.",
            )}
          </div>
          {text(
            "Credential reference",
            "credential_env",
            "A name such as OPENAI_API_KEY. Save its secret value in Credentials, not here.",
          )}
          {text(
            "Base URL",
            "base_url",
            "Optional HTTP(S) endpoint without credentials, query or fragment.",
          )}
          <p className="config-tip">
            A valid definition does not probe provider connectivity. New Runs
            use this model after the file is loaded.
          </p>
        </>
      );
      break;
    case "environments":
      content = (
        <>
          <label>
            Environment provider
            <select
              value={String(value.kind ?? "local")}
              onChange={(e) => {
                // Change only the provider here; incompatible authored container fields remain visible for repair.
                update(["kind"], e.target.value);
              }}
            >
              <option value="local">Local</option>
              <option value="docker">Docker</option>
            </select>
          </label>
          {(value.kind === "docker" || value.image || value.user) && (
            <div className="config-columns">
              {text(
                "Docker image",
                "image",
                "Required for Docker; clear for Local.",
              )}
              {text(
                "Container user",
                "user",
                "Optional UID:GID or user; clear for Local.",
              )}
            </div>
          )}
          <div className="config-columns">
            {choice(
              "File authority",
              ["files"],
              ["none", "read", "write"],
              value.files,
              "read",
            )}
            {choice(
              "Idle retention",
              ["retention"],
              ["keep", "stop"],
              value.retention,
              "keep",
            )}
          </div>
          {toggle("Allow shell execution", "shell")}
          <p className="config-tip">
            Local shell runs with the server account's authority. Every Thread
            uses the Instance workspace; Docker does not make shared working
            files private.
          </p>
        </>
      );
      break;
    case "profiles":
      content = (
        <>
          <div className="config-columns">
            {choice("Model", ["model_id"], ids("models"), value.model_id)}
            {choice(
              "Environment",
              ["environment_id"],
              ids("environments"),
              value.environment_id,
            )}
          </div>
          <label>
            Agent instructions
            {typeof agent.instructions === "string" ||
            agent.instructions == null ? (
              <textarea
                rows={7}
                value={String(agent.instructions ?? "")}
                onChange={(e) =>
                  update(["agent", "instructions"], e.target.value)
                }
              />
            ) : (
              <small>
                This native instruction structure is editable in YAML.
              </small>
            )}
          </label>
          <Section
            title="Usage limits"
            description="Limits belong to this profile's native agent configuration."
          >
            <label className="check-label">
              <input
                type="checkbox"
                checked={limits.request_limit !== null}
                onChange={(e) =>
                  update(
                    ["agent", "usage_limits", "request_limit"],
                    e.target.checked ? 50 : null,
                  )
                }
              />
              Limit model requests
            </label>
            <div className="config-columns">
              {limits.request_limit !== null &&
                number(
                  "Request limit",
                  ["agent", "usage_limits", "request_limit"],
                  limits.request_limit ?? 50,
                  "Default: 50 model requests per Run.",
                )}
              {number(
                "Tool call limit",
                ["agent", "usage_limits", "tool_calls_limit"],
                limits.tool_calls_limit,
                "Blank uses the native default.",
              )}
              {number(
                "Input token limit",
                ["agent", "usage_limits", "input_tokens_limit"],
                limits.input_tokens_limit,
                "Optional input-token budget.",
              )}
              {number(
                "Output token limit",
                ["agent", "usage_limits", "output_tokens_limit"],
                limits.output_tokens_limit,
                "Optional output-token budget.",
              )}
            </div>
          </Section>
          <Section
            title="Tool permissions"
            description="Rules use stable permission IDs, not visible function names. Current client grants and environment authority still apply."
          >
            {choice(
              "Default tool policy",
              ["permissions", "default"],
              ["inherit", "allow", "ask", "review", "deny"],
              permissions.default,
              "inherit",
            )}
            <Mapping
              label="Permission rules"
              value={fields(permissions.rules)}
              update={(path, item) =>
                update(["permissions", "rules", ...path], item)
              }
              options={["inherit", "allow", "ask", "review", "deny"]}
              keyHint="claw.files.retain"
            />
          </Section>
          {references("Skills", "skills", "skills")}
          {references("MCP servers", "mcp_servers", "mcp")}
          {references("Child profiles", "child_profiles", "profiles")}
          <details>
            <summary>Model tuning</summary>
            <div className="config-columns">
              {number(
                "Temperature",
                ["agent", "model_settings", "temperature"],
                settings.temperature,
                "Provider-dependent sampling setting.",
                0,
                "any",
              )}
              {number(
                "Maximum output tokens",
                ["agent", "model_settings", "max_tokens"],
                settings.max_tokens,
                "Provider-dependent output cap.",
              )}
            </div>
            <p className="subtle">
              Additional native agent settings remain available in YAML.
            </p>
          </details>
        </>
      );
      break;
    case "skills":
      content = (
        <>
          {text(
            "Skill name",
            "name",
            "Lowercase letters, numbers and hyphens.",
          )}
          {text("Description", "description")}
          {text("Instructions", "instructions", undefined, true)}
          <Mapping
            label="Supporting files"
            value={fields(value.files)}
            update={(path, item) => update(["files", ...path], item)}
            multiline
            keyHint="references/example.md"
          />
        </>
      );
      break;
    case "mcp":
      content = (
        <>
          {text("Streamable HTTP endpoint", "url")}
          {text("Description", "description")}
          <Mapping
            label="Header credential references"
            value={fields(value.header_env)}
            update={(path, item) => update(["header_env", ...path], item)}
            keyHint="Authorization"
          />
          <p className="subtle">
            Map each header to a credential reference name. Store complete
            header values separately.
          </p>
          <label className="check-label">
            <input
              type="checkbox"
              checked={value.allowed_tools == null}
              onChange={(e) =>
                update(["allowed_tools"], e.target.checked ? undefined : [])
              }
            />
            Allow all tools reported by the server
          </label>
          {value.allowed_tools != null && (
            <ListField
              label="Allowed tool names"
              value={value.allowed_tools}
              change={(items) => update(["allowed_tools"], items)}
              hint="An empty list allows no tools."
            />
          )}
        </>
      );
      break;
    case "channels":
      content = (
        <>
          {toggle("Channel enabled", "enabled", true)}
          <div className="config-columns">
            {text(
              "Platform",
              "platform",
              "Identity namespace, for example http.",
            )}
            {text("Account", "account")}
          </div>
          {text("External channel", "channel")}
          <div className="config-columns">
            {text(
              "Ingress client ID",
              "ingress_actor_id",
              "Create a client in Access first.",
            )}
            {text(
              "Execution actor ID",
              "execution_actor_id",
              "Default: operator.",
            )}
          </div>
          <ListField
            label="Allowed sender IDs"
            value={value.allowed_senders}
            change={(items) => update(["allowed_senders"], items)}
            hint="At least one explicit external sender is required."
          />
          {toggle("Accept inbound messages", "inbound", true)}
          {toggle("This is a group conversation", "group")}
          {toggle(
            "Require an explicit mention in groups",
            "require_addressed",
            true,
          )}
          {toggle(
            "These participants may share Main's context",
            "shared_context_acknowledged",
            false,
            "Required for enabled Channels in One Thread mode. Main shares knowledge across all participating Channels.",
          )}
          {choice(
            "Per-Channel profile",
            ["profile_id"],
            ids("profiles"),
            value.profile_id,
          )}
          <Section
            title="Outbound delivery"
            description="In One Thread mode, only Main can publish externally."
          >
            {toggle("Allow outbound delivery", "outbound")}
            {text(
              "Delivery URL",
              "delivery_url",
              "Required when outbound delivery is enabled.",
            )}
            {text("Delivery credential reference", "credential_env")}
          </Section>
        </>
      );
      break;
    case "defaults":
      content = (
        <>
          {choice(
            "Default profile",
            ["profile_id"],
            ids("profiles"),
            value.profile_id,
          )}
          <p className="config-tip">
            Used when new work has no explicit profile selection. Existing
            Threads keep their selected profile; accepted Runs keep their
            captured composition.
          </p>
        </>
      );
      break;
    case "server":
      content = (
        <>
          <p className="config-tip">
            These settings take effect at startup. CLI flags and environment
            overrides may take precedence. Reload does not move the running
            Instance or switch its mode.
          </p>
          <div className="config-columns">
            {text("Listen host", "host", "Default: 127.0.0.1.")}
            {number(
              "Listen port",
              ["port"],
              value.port ?? 8080,
              "Default: 8080.",
              1,
            )}
          </div>
          {text(
            "Data root",
            "data_root",
            "Protected state directory. Relative paths resolve from this file's directory.",
          )}
          {text(
            "Shared workspace",
            "workspace",
            "Separate from the data root. Default: startup working directory.",
          )}
          {number(
            "Concurrent Runs",
            ["concurrency"],
            value.concurrency ?? 4,
            "Between 1 and 64; each Thread remains serialized.",
            1,
          )}
          {choice(
            "Conversation mode",
            ["mode"],
            ["one_thread", "per_channel"],
            value.mode,
          )}
          <p className="subtle">
            Not set retains the saved mode. New Instances default to one_thread.
          </p>
          {toggle("Serve the Console", "console", true)}
        </>
      );
      break;
  }
  return (
    <div className="config-fields">
      {content}
      {resourceKind[section] && (
        <div className="config-retirement">
          <label className="check-label">
            <input
              type="checkbox"
              checked={retired.includes(retiredKey)}
              onChange={(e) =>
                patch(
                  ["retired"],
                  e.target.checked
                    ? [...retired, retiredKey]
                    : retired.filter((item) => item !== retiredKey),
                )
              }
            />
            Retire from future selection
          </label>
          <small>
            Retain this definition in the file without changing historical Runs.
          </small>
        </div>
      )}
    </div>
  );
}
function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="config-field-section">
      <h3>{title}</h3>
      <p className="subtle">{description}</p>
      {children}
    </section>
  );
}
function ListField({
  label,
  value,
  change,
  hint,
}: {
  label: string;
  value: unknown;
  change: (value: string[]) => void;
  hint: string;
}) {
  const items = Array.isArray(value) ? (value as string[]) : [];
  const [addition, setAddition] = useState("");
  return (
    <fieldset className="config-choices">
      <legend>{label}</legend>
      {items.map((item, index) => (
        <div className="config-map-row" key={index}>
          <input
            aria-label={`${label} ${index + 1}`}
            value={item}
            onChange={(e) =>
              change(
                items.map((old, i) => (i === index ? e.target.value : old)),
              )
            }
          />
          <button
            aria-label={`Remove ${item}`}
            onClick={() => change(items.filter((_, i) => i !== index))}
          >
            Remove
          </button>
        </div>
      ))}
      <div className="config-map-row">
        <input
          aria-label={`New ${label}`}
          value={addition}
          onChange={(e) => setAddition(e.target.value)}
        />
        <button
          disabled={!addition.trim() || items.includes(addition.trim())}
          onClick={() => {
            change([...items, addition.trim()]);
            setAddition("");
          }}
        >
          Add
        </button>
      </div>
      <small>{hint}</small>
    </fieldset>
  );
}
function Mapping({
  label,
  value,
  update,
  keyHint,
  options,
  multiline = false,
}: {
  label: string;
  value: Fields;
  update: (path: string[], value: unknown) => void;
  keyHint: string;
  options?: string[];
  multiline?: boolean;
}) {
  const [key, setKey] = useState("");
  return (
    <fieldset className="config-choices">
      <legend>{label}</legend>
      {Object.entries(value).map(([name, item]) => (
        <div key={name} className="config-map-entry">
          <label>
            {name}
            {options ? (
              <select
                value={String(item)}
                onChange={(e) => update([name], e.target.value)}
              >
                {!options.includes(String(item)) && (
                  <option>{String(item)}</option>
                )}
                {options.map((option) => (
                  <option key={option}>{option}</option>
                ))}
              </select>
            ) : multiline ? (
              <textarea
                rows={4}
                value={String(item)}
                onChange={(e) => update([name], e.target.value)}
              />
            ) : (
              <input
                value={String(item)}
                onChange={(e) => update([name], e.target.value)}
              />
            )}
          </label>
          <button
            aria-label={`Remove ${name}`}
            onClick={() => update([name], undefined)}
          >
            Remove
          </button>
        </div>
      ))}
      <div className="config-map-row">
        <input
          aria-label={`New ${label} key`}
          placeholder={keyHint}
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
        <button
          disabled={!key.trim() || key.trim() in value}
          onClick={() => {
            update([key.trim()], options?.[0] ?? "");
            setKey("");
          }}
        >
          Add entry
        </button>
      </div>
    </fieldset>
  );
}
