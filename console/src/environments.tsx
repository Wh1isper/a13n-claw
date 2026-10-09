import { useState } from "react";
import {
  Api,
  useRemote,
  type Principal,
  type Run,
  type Target,
  type Thread,
} from "./api";
import { Action, Empty, ErrorNotice, JsonDetails, Status } from "./components";

export function Environments({
  api,
  actor,
  thread,
}: {
  api: Api;
  actor: Principal;
  thread?: Thread;
}) {
  const targets = useRemote<Target[]>(
    api,
    thread ? `/threads/${thread.id}/targets` : null,
    2500,
  );
  const runs = useRemote<Run[]>(
    api,
    thread ? `/threads/${thread.id}/runs` : null,
    2500,
  );
  const [selected, setSelected] = useState("");
  const run =
    runs.data?.find((item) => item.id === selected) ?? runs.data?.at(-1);
  return (
    <div>
      <header className="page-heading">
        <div>
          <p className="eyebrow">SHARED WORKSPACE · SCOPED ACCESS</p>
          <h1>Environments</h1>
          <p>
            {thread
              ? `Managed targets for ${thread.title}`
              : "Select a Thread to inspect its working context."}
          </p>
        </div>
      </header>
      <div className="notice">
        All Threads share the Instance workspace. Removing a target discards its
        container layer, not the shared workspace or saved conversation. File
        access below follows a selected Run's captured environment.
      </div>
      <ErrorNotice>{targets.error || runs.error}</ErrorNotice>
      {thread && targets.data?.length === 0 && (
        <Empty title="No target prepared yet.">
          The first admitted Run prepares its captured environment.
          Configuration alone does not prove readiness.
        </Empty>
      )}
      {targets.data?.map((target) => (
        <section className="panel" key={target.id}>
          <div className="section-heading">
            <h2>
              {target.definition.kind === "docker"
                ? "Docker target"
                : "Local environment"}
            </h2>
            <Status value={target.status} />
          </div>
          <p className="subtle">
            <code>{target.id}</code>
            <br />
            Generation {target.generation.slice(0, 16)}
          </p>
          {target.error && <ErrorNotice>{target.error}</ErrorNotice>}
          <JsonDetails
            title="Captured target definition"
            value={target.definition}
          />
          {actor.admin && (
            <div className="toolbar">
              {["inspect", "prepare", "stop", "remove"].map((operation) => (
                <Action
                  key={operation}
                  danger={operation === "remove"}
                  run={async () => {
                    if (
                      operation === "remove" &&
                      !confirm(
                        "Remove this target? Container-local files and processes may be lost. Shared workspace data and Thread history remain.",
                      )
                    )
                      return;
                    await api.send(`/targets/${target.id}/manage`, {
                      operation,
                    });
                    targets.reload();
                  }}
                >
                  {operation[0].toUpperCase() + operation.slice(1)}
                </Action>
              ))}
            </div>
          )}
        </section>
      ))}
      {run && (
        <section className="panel">
          <h2>Workspace files</h2>
          <label>
            Captured Run
            <select
              value={run.id}
              onChange={(event) => setSelected(event.target.value)}
            >
              {runs.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id.slice(-8)} · {item.composition.environment.id} ·{" "}
                  {item.status}
                </option>
              ))}
            </select>
          </label>
          <p className="subtle">
            Read-only browser · logical root /workspace ·{" "}
            {run.composition.environment.id}. A stopped target must be prepared
            explicitly; no host fallback.
          </p>
          <FileBrowser key={run.id} api={api} run={run} />
        </section>
      )}
    </div>
  );
}
function FileBrowser({ api, run }: { api: Api; run: Run }) {
  const [directory, setDirectory] = useState("/workspace");
  const [offset, setOffset] = useState(0);
  const [file, setFile] = useState("");
  const [line, setLine] = useState(0);
  const entries = useRemote<{
    entries: { path: string; kind: string; size: number | null }[];
    offset: number;
    has_more: boolean;
  }>(
    api,
    `/runs/${run.id}/files?path=${encodeURIComponent(directory)}&offset=${offset}`,
  );
  const text = useRemote<{
    text: string;
    line_offset: number;
    lines_read: number;
    has_more: boolean;
    truncated_lines: number[];
  }>(
    api,
    file
      ? `/runs/${run.id}/file-content?path=${encodeURIComponent(file)}&line_offset=${line}`
      : null,
  );
  const openDirectory = (path: string) => {
    setDirectory(path);
    setOffset(0);
    setFile("");
  };
  return (
    <div>
      <div className="toolbar">
        <button
          disabled={directory === "/workspace"}
          onClick={() =>
            openDirectory(
              directory.slice(0, directory.lastIndexOf("/")) || "/workspace",
            )
          }
        >
          Parent
        </button>
        <code>{directory}</code>
        <button
          onClick={() => {
            entries.reload();
            text.reload();
          }}
        >
          Refresh files
        </button>
      </div>
      <ErrorNotice>{entries.error || text.error}</ErrorNotice>
      <div className="file-list">
        {entries.data?.entries.map((entry) => (
          <button
            key={entry.path}
            className="file-row"
            onClick={() => {
              if (entry.kind === "directory") openDirectory(entry.path);
              else {
                setFile(entry.path);
                setLine(0);
              }
            }}
          >
            <span>{entry.path.split("/").at(-1)}</span>
            <small>
              {entry.kind} · {entry.size ?? "—"}
            </small>
          </button>
        ))}
      </div>
      <div className="toolbar">
        <button
          disabled={!offset}
          onClick={() => setOffset(Math.max(0, offset - 200))}
        >
          Previous files
        </button>
        <button
          disabled={!entries.data?.has_more}
          onClick={() =>
            setOffset(offset + (entries.data?.entries.length ?? 0))
          }
        >
          More files
        </button>
      </div>
      {file && (
        <div className="file-preview">
          <h3>{file}</h3>
          {text.data && (
            <>
              <pre>{text.data.text}</pre>
              <small>
                Lines {text.data.line_offset + 1}–
                {text.data.line_offset + text.data.lines_read}
                {text.data.truncated_lines.length > 0 &&
                  " · some long lines truncated"}
              </small>
              <div className="toolbar">
                <button
                  disabled={!line}
                  onClick={() => setLine(Math.max(0, line - 200))}
                >
                  Previous lines
                </button>
                <button
                  disabled={!text.data.has_more}
                  onClick={() => setLine(line + text.data!.lines_read)}
                >
                  More lines
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
