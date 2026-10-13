import { useEffect, useRef, useState } from "react";
import { Api, ApiError, message } from "./api";
import { ErrorNotice } from "./components";
import { ConfigurationFields } from "./configuration-fields";
import {
  fields,
  patchSource,
  readSource,
  resourceKind,
  sections,
  starter,
  templates,
  type ConfigurationStatus,
  type Preview,
} from "./configuration-document";

type Draft = { text: string; base: ConfigurationStatus };
type Write = { text: string; expected_revision: string };

export function ConfigurationEditor({
  api,
  active,
  changed,
}: {
  api: Api;
  active: boolean;
  changed: () => void;
}) {
  const [disk, setDisk] = useState<ConfigurationStatus>();
  const [draft, setDraft] = useState<Draft>();
  const [section, setSection] = useState("models");
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [sourceMode, setSourceMode] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [pending, setPending] = useState<Write>();
  const [preview, setPreview] = useState<{ text: string; result: Preview }>();
  const [refresh, setRefresh] = useState(0);
  const generation = useRef(0);
  const pendingRef = useRef(pending);
  pendingRef.current = pending;
  const dirty = !!draft && draft.text !== draft.base.text;
  const conflict = !!disk && !!draft && disk.revision !== draft.base.revision;
  const parsed = readSource(draft?.text ?? "");
  const catalog = parsed?.value ?? {};
  const singular = section === "server" || section === "defaults";
  const entries = fields(catalog[section]);
  const currentId =
    selected in entries ? selected : (Object.keys(entries)[0] ?? "");
  const value = singular ? entries : fields(entries[currentId]);
  const description = sections.find((item) => item.key === section)!;
  const hasResources = sections.some(
    (item) =>
      item.key !== "server" && Object.keys(fields(catalog[item.key])).length,
  );

  useEffect(() => {
    if (!active) return;
    let alive = true;
    const request = ++generation.current;
    void api
      .get<ConfigurationStatus>("/configuration")
      .then((status) => {
        if (!alive || request !== generation.current) return;
        setDisk(status);
        const submitted = pendingRef.current;
        if (submitted && status.text === submitted.text) {
          setDraft((previous) =>
            previous
              ? { text: previous.text, base: status }
              : { text: status.text, base: status },
          );
          setPending(undefined);
          setNotice(
            "The submitted file is saved. Your newer edits are still a draft.",
          );
        } else {
          setDraft(
            (previous) => previous ?? { text: status.text, base: status },
          );
        }
        setError("");
      })
      .catch((reason) => {
        if (alive && request === generation.current) setError(message(reason));
      });
    return () => {
      alive = false;
    };
  }, [api, active, refresh]);

  useEffect(() => {
    if (!dirty && !pending) return;
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty, pending]);

  const edit = (text: string) => {
    setDraft((previous) => previous && { ...previous, text });
    setNotice("");
  };
  const patch = (path: string[], value: unknown) => {
    if (!draft) return;
    try {
      edit(patchSource(draft.text, path, value));
    } catch (reason) {
      setError(message(reason));
    }
  };
  const save = async (request: Write) => {
    ++generation.current;
    setBusy("save");
    setError("");
    setNotice("");
    setPending(request);
    try {
      const status = await api.send<ConfigurationStatus>(
        "/configuration",
        request,
        "PUT",
      );
      setDisk(status);
      if (status.text === request.text) {
        setDraft(
          (previous) => previous && { text: previous.text, base: status },
        );
        setPending(undefined);
        setNotice(
          "File saved. Load the saved file when you are ready to use it for new work.",
        );
      } else {
        setError(
          "The source changed after saving. Compare the current file before continuing.",
        );
      }
    } catch (reason) {
      if (
        reason instanceof ApiError &&
        reason.status >= 400 &&
        reason.status < 500
      )
        setPending(undefined);
      setError(message(reason));
    } finally {
      ++generation.current;
      setBusy("");
    }
  };
  const inspect = async () => {
    if (!draft) return;
    const text = draft.text;
    setBusy("validate");
    setError("");
    try {
      const result = await api.send<Preview>("/configuration/validate", {
        text,
      });
      setPreview({ text, result });
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy("");
    }
  };
  const load = async () => {
    if (!disk) return;
    ++generation.current;
    setBusy("load");
    setError("");
    setNotice("");
    try {
      const status = await api.send<ConfigurationStatus>(
        "/configuration/reload",
        { expected_revision: disk.revision },
      );
      setDisk(status);
      setNotice(
        status.pending
          ? "Loaded the requested revision. The file has changed again; inspect its newer source."
          : "Saved file loaded. Accepted Runs keep their captured definitions.",
      );
      changed();
    } catch (reason) {
      setError(message(reason));
    } finally {
      ++generation.current;
      setBusy("");
    }
  };
  const adopt = () => {
    if (
      !disk ||
      ((dirty || pending) &&
        !confirm("Discard this local draft and use the current file?"))
    )
      return;
    setDraft({ text: disk.text, base: disk });
    setPending(undefined);
    setPreview(undefined);
    setError("");
    setNotice("");
  };
  const rebase = () => {
    if (
      !disk ||
      !draft ||
      !confirm(
        "Keep your draft and use the displayed file revision as its new baseline? Saving will replace that file with your draft.",
      )
    )
      return;
    setDraft({ text: draft.text, base: disk });
    setPending(undefined);
    setError("");
  };
  const remove = () => {
    if (
      !draft ||
      !confirm(
        `Remove ${currentId} from the file? Loading will ${section === "channels" ? "disable this Channel" : "retire this resource"}. Historical Runs remain available.`,
      )
    )
      return;
    let text = patchSource(draft.text, [section, currentId], undefined);
    const retired = Array.isArray(catalog.retired) ? catalog.retired : [];
    if (resourceKind[section])
      text = patchSource(
        text,
        ["retired"],
        retired.filter(
          (item) => item !== `${resourceKind[section]}/${currentId}`,
        ),
      );
    edit(text);
    setSelected("");
  };
  const currentPreview =
    preview && preview.text === draft?.text ? preview.result : undefined;
  return (
    <section
      className="configuration-workbench"
      aria-label="File configuration"
    >
      <div className="config-source-card">
        <div className="config-source-title">
          <h2>Configuration file</h2>
          <span
            className={`config-state ${dirty || disk?.pending ? "config-state-pending" : ""}`}
          >
            {dirty
              ? "Unsaved draft"
              : disk?.pending
                ? "Saved · not loaded"
                : disk
                  ? "Loaded"
                  : "Connecting"}
          </span>
        </div>
        <code className="config-path">
          {disk?.path ?? "Reading the server's selected file…"}
        </code>
        <p>
          Edit with forms or YAML. Both share one draft. Save writes the file;
          load updates the runtime for new work.
        </p>
        <div className="config-revisions">
          <span>
            File <code>{disk?.revision.slice(0, 12) ?? "—"}</code>
          </span>
          <span>
            Loaded <code>{disk?.active_revision?.slice(0, 12) ?? "none"}</code>
          </span>
          <button
            disabled={!!busy}
            onClick={() => setRefresh((value) => value + 1)}
          >
            Refresh status
          </button>
        </div>
        {disk && disk.issues.length > 0 && (
          <div className="config-source-issues" role="status">
            <strong>The saved file needs repair</strong>
            {disk.issues.map((issue, index) => (
              <p key={index}>
                <code>{issue.path}</code> {issue.message}
              </p>
            ))}
          </div>
        )}
        {disk && disk.restart_required.length > 0 && (
          <p className="config-tip">
            Restart required for{" "}
            <strong>{disk.restart_required.join(", ")}</strong>. Running launch
            settings stay unchanged.
          </p>
        )}
      </div>
      <ErrorNotice>{error}</ErrorNotice>
      {notice && (
        <p className="config-feedback" role="status">
          {notice}
        </p>
      )}
      {pending && (
        <div className="config-conflict">
          <strong>Save needs reconciliation</strong>
          <p>
            The response did not confirm the current file. Refresh status to
            compare it with the submitted draft, or retry that exact save. New
            typing remains here.
          </p>
          <button disabled={!!busy} onClick={() => void save(pending)}>
            Retry original save
          </button>
        </div>
      )}
      {conflict && (
        <div className="config-conflict">
          <strong>The file changed outside this draft</strong>
          <p>
            Your input is preserved. Compare the current file before choosing
            which content to keep.
          </p>
          <details>
            <summary>Compare with current file</summary>
            <pre>{disk?.text}</pre>
          </details>
          <div className="toolbar">
            <button disabled={!!busy} onClick={adopt}>
              Use current file
            </button>
            <button disabled={!!busy} onClick={rebase}>
              Keep draft on this revision
            </button>
          </div>
        </div>
      )}
      {draft && (
        <>
          {!hasResources && parsed && (
            <div className="config-onboarding">
              <div>
                <h3>Build your first agent configuration</h3>
                <p>
                  Start with a model, a read-only Local environment and a
                  default profile. Review the model name and save its credential
                  separately.
                </p>
              </div>
              <button
                onClick={() => {
                  edit(starter(draft.text));
                  setSection("models");
                  setSelected("primary");
                }}
              >
                Add starter definitions
              </button>
            </div>
          )}
          <div className="config-editor-layout">
            <nav className="config-nav" aria-label="Configuration sections">
              {sections.map((item) => (
                <button
                  key={item.key}
                  aria-current={section === item.key ? "page" : undefined}
                  onClick={() => {
                    setSection(item.key);
                    setSelected("");
                    setName("");
                  }}
                >
                  <span>{item.label}</span>
                  {item.key !== "server" && item.key !== "defaults" && (
                    <small>
                      {Object.keys(fields(catalog[item.key])).length}
                    </small>
                  )}
                </button>
              ))}
            </nav>
            <div className="config-editor-body">
              <div className="config-editor-heading">
                <div>
                  <h2>{description.label}</h2>
                  <p>{description.detail}</p>
                </div>
                <div
                  className="config-view-toggle"
                  role="group"
                  aria-label="Editor view"
                >
                  <button
                    aria-pressed={!sourceMode}
                    onClick={() => setSourceMode(false)}
                  >
                    Form
                  </button>
                  <button
                    aria-pressed={sourceMode}
                    onClick={() => setSourceMode(true)}
                  >
                    YAML
                  </button>
                </div>
              </div>
              {sourceMode ? (
                <label className="config-source-label">
                  Full configuration source
                  <textarea
                    spellCheck={false}
                    className="code-editor config-yaml"
                    rows={24}
                    value={draft.text}
                    onChange={(e) => edit(e.target.value)}
                  />
                  <small>
                    All sections use this exact draft. Keep credential values
                    outside the file.
                  </small>
                </label>
              ) : !parsed ? (
                <div className="config-empty">
                  <h3>Repair the source to continue</h3>
                  <p>
                    The YAML must contain a mapping with unique keys. Your
                    source is still available; switch to YAML to correct it.
                  </p>
                  <button onClick={() => setSourceMode(true)}>
                    Edit YAML source
                  </button>
                </div>
              ) : (
                <>
                  {!singular && (
                    <>
                      <div
                        className="config-resource-tabs"
                        role="group"
                        aria-label={`${description.label} definitions`}
                      >
                        {Object.keys(entries).map((id) => (
                          <button
                            key={id}
                            aria-pressed={currentId === id}
                            onClick={() => setSelected(id)}
                          >
                            {id}
                          </button>
                        ))}
                      </div>
                      <div className="config-add">
                        <input
                          aria-label="New resource ID"
                          placeholder={`New ${resourceKind[section] ?? "channel"} ID`}
                          value={name}
                          onChange={(e) => setName(e.target.value)}
                        />
                        <button
                          disabled={
                            !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(name) ||
                            name in entries
                          }
                          onClick={() => {
                            patch([section, name], templates[section]);
                            setSelected(name);
                            setName("");
                          }}
                        >
                          Add definition
                        </button>
                      </div>
                    </>
                  )}
                  {singular || currentId ? (
                    <>
                      <div className="config-definition-title">
                        <h3>{singular ? "Instance settings" : currentId}</h3>
                        {!singular && (
                          <button
                            className="text-button danger"
                            onClick={remove}
                          >
                            Remove from file
                          </button>
                        )}
                      </div>
                      <ConfigurationFields
                        key={`${section}/${currentId}`}
                        section={section}
                        id={singular ? "instance" : currentId}
                        value={value}
                        catalog={catalog}
                        patch={patch}
                        update={(path, value) =>
                          patch(
                            singular
                              ? [section, ...path]
                              : [section, currentId, ...path],
                            value,
                          )
                        }
                      />
                    </>
                  ) : (
                    <div className="config-empty">
                      <h3>No {description.label.toLowerCase()} yet</h3>
                      <p>
                        Add a named definition above. You can review the full
                        source at any time.
                      </p>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
          {currentPreview && (
            <div className="config-preview" aria-live="polite">
              <h3>
                {currentPreview.valid
                  ? "Draft validated"
                  : "Check these fields"}
              </h3>
              {currentPreview.issues.map((issue, index) => (
                <p key={index}>
                  <code>{issue.path}</code> {issue.message}{" "}
                  <small>({issue.code})</small>
                </p>
              ))}
              {currentPreview.valid && (
                <>
                  <p className="subtle">
                    Validation checks definitions and dependencies, not provider
                    connectivity.
                  </p>
                  {currentPreview.changes.length ? (
                    <ul>
                      {currentPreview.changes.map((change) => (
                        <li key={change.resource}>
                          <span>{change.action}</span>
                          <code>{change.resource}</code>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>No runtime resource changes.</p>
                  )}
                </>
              )}
            </div>
          )}
          <div className="config-savebar">
            <div>
              <strong>
                {dirty
                  ? "Draft changes"
                  : disk?.pending
                    ? "Ready to load"
                    : "Up to date"}
              </strong>
              <small>
                Drafts stay in this tab until page reload or disconnect.
              </small>
            </div>
            <div className="toolbar">
              <button disabled={!!busy} onClick={() => void inspect()}>
                {busy === "validate" ? "Checking…" : "Validate & preview"}
              </button>
              <button
                disabled={!!busy || !dirty || !!pending || conflict}
                className="primary"
                onClick={() =>
                  void save({
                    text: draft.text,
                    expected_revision: draft.base.revision,
                  })
                }
              >
                {busy === "save" ? "Saving…" : "Save file"}
              </button>
              <button
                disabled={
                  !!busy || dirty || !!pending || conflict || !disk?.pending
                }
                onClick={() => void load()}
              >
                {busy === "load" ? "Loading…" : "Load saved file"}
              </button>
              {dirty && (
                <button disabled={!!busy} onClick={adopt}>
                  Discard draft
                </button>
              )}
            </div>
          </div>
        </>
      )}
    </section>
  );
}
