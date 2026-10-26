import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { data, type Schema } from "../api/client";
import { useApi } from "../api/session";
import { ErrorNotice, Field, Modal } from "../components/ui";

type Definition = Schema["ScheduleDefinition"];
const initial: Definition = {
  name: "",
  prompt: "",
  project_id: "",
  enabled: true,
  timing: { kind: "cron", timezone: "UTC", cron: "0 9 * * *" },
  overlap: "queue",
  missed: "latest",
  grace_seconds: 300,
  notification: "never",
  destination: null,
  author: "operator",
};
export function ScheduleEditor({
  id,
  definition,
  digest,
  close,
}: {
  id?: string;
  definition?: Definition;
  digest: string | null;
  close: () => void;
}) {
  const api = useApi();
  const cache = useQueryClient();
  const [scheduleId, setScheduleId] = useState(id ?? "");
  const [draft, setDraft] = useState<Definition>(() =>
    structuredClone(definition ?? initial),
  );
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => data(api.GET("/api/projects", { signal })),
  });
  const save = useMutation({
    mutationFn: () =>
      data(
        api.PUT("/api/schedules/{schedule_id}", {
          params: { path: { schedule_id: scheduleId } },
          body: { definition: draft, expected_digest: digest },
        }),
      ),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["schedules"] });
      close();
    },
  });
  const preview = useMutation({
    mutationFn: (timing: Schema["Timing"]) =>
      data(api.POST("/api/schedules/preview", { body: { timing, count: 5 } })),
  });
  const set = <K extends keyof Definition>(key: K, value: Definition[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const timing = draft.timing;
  const previewCurrent =
    JSON.stringify(preview.variables) === JSON.stringify(timing);
  return (
    <Modal
      title={id ? `Edit ${definition?.name}` : "Create schedule"}
      description="Each occurrence runs in its own Thread. Timing and delivery use the server's accepted schedule definition."
      close={() => {
        if (!save.isPending) close();
      }}
    >
      <form
        className="form"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <fieldset className="form" disabled={save.isPending}>
          <div className="form-grid">
            <Field
              label="Schedule ID"
              hint="Stable ID; letters, numbers, hyphens and underscores."
            >
              <input
                required
                maxLength={80}
                pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,79}"
                value={scheduleId}
                disabled={!!id}
                onChange={(event) => setScheduleId(event.target.value)}
              />
            </Field>
            <Field label="Name">
              <input
                required
                maxLength={200}
                value={draft.name}
                onChange={(event) => set("name", event.target.value)}
              />
            </Field>
          </div>
          <Field label="Prompt">
            <textarea
              required
              maxLength={100000}
              rows={5}
              value={draft.prompt}
              onChange={(event) => set("prompt", event.target.value)}
            />
          </Field>
          <Field
            label="Project"
            hint="The Project owns environment, roots and shared memory for this work."
          >
            <input
              required
              list="schedule-projects"
              pattern="project-.+"
              value={draft.project_id}
              onChange={(event) => set("project_id", event.target.value)}
            />
            <datalist id="schedule-projects">
              {projects.data?.map((project) => (
                <option key={project.project_id} value={project.project_id}>
                  {project.name}
                </option>
              ))}
            </datalist>
          </Field>
          <div className="form-grid">
            <Field label="Timing">
              <select
                value={timing.kind}
                onChange={(event) => {
                  const kind = event.target.value as Schema["Timing"]["kind"];
                  set("timing", {
                    kind,
                    timezone: timing.timezone,
                    ...(kind === "cron"
                      ? { cron: "0 9 * * *" }
                      : kind === "interval"
                        ? { seconds: 3600 }
                        : { at: "" }),
                  });
                }}
              >
                <option value="cron">Cron</option>
                <option value="interval">Interval</option>
                <option value="once">Once</option>
              </select>
            </Field>
            <Field
              label="Timezone"
              hint="IANA timezone, for example Asia/Shanghai."
            >
              <input
                required
                value={timing.timezone ?? "UTC"}
                onChange={(event) =>
                  set("timing", { ...timing, timezone: event.target.value })
                }
              />
            </Field>
          </div>
          {timing.kind === "cron" && (
            <Field
              label="Cron expression"
              hint="Five fields: minute, hour, day, month, weekday."
            >
              <input
                required
                value={timing.cron ?? ""}
                onChange={(event) =>
                  set("timing", { ...timing, cron: event.target.value })
                }
              />
            </Field>
          )}
          {timing.kind === "interval" && (
            <Field label="Interval (seconds)">
              <input
                type="number"
                required
                min={60}
                max={31536000}
                value={timing.seconds ?? ""}
                onChange={(event) =>
                  set("timing", {
                    ...timing,
                    seconds: event.target.valueAsNumber,
                  })
                }
              />
            </Field>
          )}
          {timing.kind !== "cron" && (
            <Field
              label={
                timing.kind === "once"
                  ? "Run at"
                  : "First occurrence (optional)"
              }
              hint="ISO date and time with an explicit offset, for example 2027-01-01T09:00:00+08:00."
            >
              <input
                required={timing.kind === "once"}
                value={timing.at ?? ""}
                onChange={(event) =>
                  set("timing", { ...timing, at: event.target.value || null })
                }
              />
            </Field>
          )}
          <div className="stack">
            <div>
              <button
                type="button"
                disabled={preview.isPending}
                onClick={() => preview.mutate(timing)}
              >
                {preview.isPending ? "Calculating…" : "Preview timing"}
              </button>
            </div>
            {previewCurrent && <ErrorNotice error={preview.error} />}
            {previewCurrent && preview.data && (
              <div className="notice">
                <p className="hint">
                  Preview in {preview.data.timezone}; actual next due appears
                  after the scheduler accepts and discovers the definition.
                </p>
                <ol>
                  {preview.data.instants.map((instant) => (
                    <li key={instant}>
                      <code>{instant}</code>
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </div>
          <div className="form-grid">
            <Field label="Result notification">
              <select
                value={draft.notification}
                onChange={(event) =>
                  set(
                    "notification",
                    event.target.value as Definition["notification"],
                  )
                }
              >
                <option value="never">No notification</option>
                <option value="always">Always</option>
                <option value="error">Errors only</option>
              </select>
            </Field>
            <Field
              label="Destination Channel"
              hint="Required for notifications."
            >
              <input
                required={draft.notification !== "never"}
                value={draft.destination?.channel_id ?? ""}
                onChange={(event) =>
                  set(
                    "destination",
                    event.target.value
                      ? { ...draft.destination, channel_id: event.target.value }
                      : null,
                  )
                }
              />
            </Field>
          </div>
          <details className="advanced">
            <summary>Execution and delivery options</summary>
            <div className="form">
              <div className="form-grid">
                <Field label="Agent ID (optional)">
                  <input
                    value={draft.agent_id ?? ""}
                    onChange={(event) =>
                      set("agent_id", event.target.value || null)
                    }
                  />
                </Field>
                <Field label="Model ID (optional)">
                  <input
                    value={draft.model_id ?? ""}
                    onChange={(event) =>
                      set("model_id", event.target.value || null)
                    }
                  />
                </Field>
                <Field label="Overlapping work">
                  <select
                    value={draft.overlap}
                    onChange={(event) =>
                      set(
                        "overlap",
                        event.target.value as Definition["overlap"],
                      )
                    }
                  >
                    <option value="queue">Queue</option>
                    <option value="skip">Skip</option>
                  </select>
                </Field>
                <Field label="Missed occurrences">
                  <select
                    value={draft.missed}
                    onChange={(event) =>
                      set("missed", event.target.value as Definition["missed"])
                    }
                  >
                    <option value="latest">Run latest</option>
                    <option value="skip">Skip</option>
                  </select>
                </Field>
                <Field label="Grace period (seconds)">
                  <input
                    type="number"
                    required
                    min={1}
                    max={86400}
                    value={draft.grace_seconds}
                    onChange={(event) =>
                      set("grace_seconds", event.target.valueAsNumber)
                    }
                  />
                </Field>
                <Field label="Reply to (optional)">
                  <input
                    disabled={!draft.destination}
                    value={draft.destination?.reply_to ?? ""}
                    onChange={(event) => {
                      if (draft.destination)
                        set("destination", {
                          ...draft.destination,
                          reply_to: event.target.value || null,
                        });
                    }}
                  />
                </Field>
              </div>
              <p className="hint">
                Author: {draft.author ?? "operator"}
                {draft.source_channel
                  ? ` · Source Channel: ${draft.source_channel}`
                  : ""}
                . Existing source identity is preserved.
              </p>
            </div>
          </details>
          <label className="check">
            <input
              type="checkbox"
              checked={draft.enabled ?? true}
              onChange={(event) => set("enabled", event.target.checked)}
            />
            Enabled
          </label>
        </fieldset>
        <ErrorNotice error={save.error} />
        {save.error && (
          <p className="hint">
            Your draft is preserved. If the source changed, close this dialog,
            reload the catalog and reopen the definition before applying a
            revised edit.
          </p>
        )}
        <div className="form-actions">
          <button type="button" disabled={save.isPending} onClick={close}>
            Cancel
          </button>
          <button className="primary" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save schedule"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
