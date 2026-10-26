import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, data, type Schema } from "../api/client";
import { useApi } from "../api/session";
import { ErrorNotice, Field, Modal, Raw } from "../components/ui";

type ModelView = Schema["ModelInventory"]["models"][number];
type JsonObject = Record<string, Schema["JsonValue"]>;
export function jsonObject(text: string): JsonObject {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Settings must be a JSON object.");
  return value as JsonObject;
}

export function SourceConflict({ error }: { error: unknown }) {
  return error instanceof ApiError && error.status === 409 ? (
    <p className="notice">
      Source changed. Copy your draft, then refresh and reopen the editor.
    </p>
  ) : null;
}

export function ModelEditor({
  view,
  choices,
  groups,
  close,
}: {
  view?: ModelView;
  choices: Schema["ModelChoices"];
  groups: Schema["ModelInventory"]["groups"];
  close: () => void;
}) {
  const api = useApi();
  const cache = useQueryClient();
  const suggestionsId = useId();
  const original = view?.resource;
  const connections = choices.connections ?? [];
  const initial = connections.find(
    (c) =>
      c.authentication === original?.authentication.kind &&
      c.provider === original?.route.split(":")[0],
  );
  const [connectionId, setConnectionId] = useState(
    original ? (initial?.id ?? "") : (connections[0]?.id ?? ""),
  );
  const connection = connections.find((c) => c.id === connectionId);
  const [id, setId] = useState(original?.id ?? "model-");
  const [name, setName] = useState(original?.name ?? "");
  const [modelId, setModelId] = useState(
    original?.route.split(":").slice(1).join(":") ??
      connection?.default_model ??
      "",
  );
  const [baseUrl, setBaseUrl] = useState(
    String(original?.model_configuration?.base_url ?? ""),
  );
  const auth = original?.authentication;
  const [credentialKind, setCredentialKind] = useState(
    auth?.kind === "api_key" && auth.credential_ref
      ? "reference"
      : "environment",
  );
  const [credential, setCredential] = useState(
    auth?.kind === "api_key"
      ? (auth.credential_ref ?? auth.env ?? "")
      : (connection?.credential_env ?? ""),
  );
  const [group, setGroup] = useState(
    auth && "group" in auth ? (auth.group ?? "") : "",
  );
  const [settings, setSettings] = useState(
    JSON.stringify(original?.settings ?? {}, null, 2),
  );
  const [configuration, setConfiguration] = useState(
    JSON.stringify(original?.model_configuration ?? {}, null, 2),
  );
  const [preset, setPreset] = useState("");
  const options = useQuery({
    queryKey: ["model-options", connectionId, modelId, baseUrl],
    enabled: !!connection && !!modelId.trim(),
    queryFn: ({ signal }) =>
      data(
        api.POST("/api/models/options", {
          signal,
          body: {
            connection: connectionId,
            model_id: modelId,
            base_url: baseUrl || null,
          },
        }),
      ),
  });
  const save = useMutation({
    mutationFn: async () => {
      if (!connection) throw new Error("Choose a supported connection.");
      const authentication: Schema["ModelAuthentication"] =
        connection.authentication === "api_key"
          ? {
              kind: "api_key",
              ...(credentialKind === "reference"
                ? { credential_ref: credential }
                : { env: credential }),
            }
          : connection.authentication === "codex_subscription" ||
              connection.authentication === "grok_subscription"
            ? { kind: connection.authentication, group: group || null }
            : { kind: connection.authentication };
      const modelConfiguration = jsonObject(configuration);
      // The base-URL field owns this key, including explicit removal.
      delete modelConfiguration.base_url;
      const recipe = await data(
        api.POST("/api/models/prepare", {
          body: {
            connection: connectionId,
            model_id: modelId,
            authentication,
            base_url: baseUrl || null,
            settings: preset ? null : jsonObject(settings),
            preset: preset || null,
            model_configuration: modelConfiguration,
            // Host serializes this enum set as strings; round-trip its validated values.
            model_characteristics: original?.model_characteristics as
              Schema["ModelCharacteristics-Input"] | null | undefined,
          },
        }),
      );
      return data(
        api.PUT("/api/models", {
          body: {
            relative_path: view?.relative_path ?? `models/${id}.yaml`,
            expected_digest: view?.source_digest ?? null,
            resource: {
              ...original,
              ...recipe,
              model_characteristics: recipe.model_characteristics as
                Schema["ModelCharacteristics-Input"] | null | undefined,
              schema_version: "1",
              kind: "model",
              id,
              name,
            },
          },
        }),
      );
    },
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: ["models"] });
      close();
    },
  });
  if (!connection)
    return (
      <Modal
        title={view ? "Model source" : "Add model"}
        description="Native connection configuration"
        close={close}
      >
        <p className="notice">
          {original
            ? "This model uses a custom connection. Edit its native configuration file to preserve its routing and authentication."
            : "No model connections are available. Refresh the inventory before trying again."}
        </p>
        {view && (
          <p>
            <code>{view.relative_path}</code>
          </p>
        )}
        {original && <Raw value={original} title="Saved resource" />}
      </Modal>
    );
  return (
    <Modal
      title={view ? "Edit model" : "Add model"}
      close={() => {
        if (!save.isPending) close();
      }}
    >
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <fieldset disabled={save.isPending} className="form">
          <div className="form-grid">
            <Field label="Name">
              <input
                required
                maxLength={256}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            <Field label="Resource ID">
              <input
                required
                pattern="model-[a-zA-Z0-9_-]+"
                value={id}
                disabled={!!view}
                onChange={(e) => setId(e.target.value)}
              />
            </Field>
          </div>
          <Field label="Connection">
            <select
              value={connectionId}
              disabled={!!view}
              onChange={(e) => {
                const next = connections.find((c) => c.id === e.target.value)!;
                setConnectionId(next.id);
                setModelId(next.default_model);
                setBaseUrl("");
                setCredential(next.credential_env ?? "");
                setGroup("");
                setPreset("");
                setSettings("{}");
                setConfiguration("{}");
              }}
            >
              {connections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Provider model ID" hint="Custom model IDs supported.">
            <input
              required
              value={modelId}
              list={suggestionsId}
              onChange={(e) => {
                setModelId(e.target.value);
                setPreset("");
              }}
            />
            <datalist id={suggestionsId}>
              {connection.models.map((m) => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </datalist>
          </Field>
          {connection.supports_base_url && (
            <Field
              label="Base URL"
              hint="Leave empty for the provider default."
            >
              <input
                type="url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
            </Field>
          )}
          {connection.authentication === "api_key" ? (
            <div className="form-grid">
              <Field label="Credential source">
                <select
                  value={credentialKind}
                  onChange={(e) => {
                    setCredentialKind(e.target.value);
                    setCredential("");
                  }}
                >
                  <option value="environment">Environment variable</option>
                  <option value="reference">Stored credential reference</option>
                </select>
              </Field>
              <Field
                label={
                  credentialKind === "reference"
                    ? "Credential reference"
                    : "Environment variable"
                }
                hint="Reference name only."
              >
                <input
                  required
                  value={credential}
                  onChange={(e) => setCredential(e.target.value)}
                />
              </Field>
            </div>
          ) : (
            (connection.authentication === "codex_subscription" ||
              connection.authentication === "grok_subscription") && (
              <Field label="Subscription group">
                <select
                  value={group}
                  onChange={(e) => setGroup(e.target.value)}
                >
                  <option value="">Provider default account</option>
                  {groups
                    .filter(
                      (g) =>
                        `${g.resource.provider}_subscription` ===
                        connection.authentication,
                    )
                    .map((g) => (
                      <option key={g.resource.id} value={g.resource.id}>
                        {g.resource.name}
                      </option>
                    ))}
                </select>
              </Field>
            )
          )}
          <ErrorNotice error={options.error} />
          {options.data && (
            <>
              <Field
                label="Settings preset"
                hint={
                  options.data.presets.find((p) => p.value === preset)
                    ?.description ?? ""
                }
              >
                <select
                  value={preset}
                  onChange={(e) => setPreset(e.target.value)}
                >
                  <option value="">Keep custom settings</option>
                  {options.data.presets.map((p) => (
                    <option key={p.value} value={p.value}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </Field>
              <p className="hint">
                Context window:{" "}
                {options.data.context_window?.toLocaleString() ?? "Unknown"} ·{" "}
                {options.data.known_capabilities
                  ? "Reviewed capabilities"
                  : "Unreviewed model"}
              </p>
            </>
          )}
          <details className="advanced">
            <summary>Advanced settings</summary>
            <div className="form">
              <Field
                label="Model settings (JSON)"
                hint={
                  preset ? "The selected preset replaces these settings." : ""
                }
              >
                <textarea
                  spellCheck={false}
                  disabled={!!preset}
                  value={settings}
                  onChange={(e) => setSettings(e.target.value)}
                />
              </Field>
              {connection.authentication === "api_key" && (
                <Field
                  label="Connection configuration (JSON)"
                  hint="Use the Base URL field for base_url."
                >
                  <textarea
                    spellCheck={false}
                    value={configuration}
                    onChange={(e) => setConfiguration(e.target.value)}
                  />
                </Field>
              )}
              {original && (
                <Raw value={original} title="Current saved resource" />
              )}
            </div>
          </details>
        </fieldset>
        <ErrorNotice error={save.error} />
        <SourceConflict error={save.error} />
        <div className="form-actions">
          <button type="button" disabled={save.isPending} onClick={close}>
            Cancel
          </button>
          <button className="primary" disabled={save.isPending || !connection}>
            {save.isPending ? "Saving…" : "Save model"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
