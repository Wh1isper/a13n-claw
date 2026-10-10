import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Api,
  allowed,
  message,
  useRemote,
  type Instance,
  type Resource,
  type Thread,
} from "./api";
import { Empty, ErrorNotice, Status } from "./components";
import { CommandProvider, usePendingCommands } from "./commands";
import { Conversation, blankDraft, type Draft } from "./conversation";
import { NewThread } from "./creation";
import { Environments } from "./environments";
import { Settings } from "./settings";
import "./styles.css";

type Page = "Overview" | "Threads" | "Environments" | "Settings";
const paths: Record<Page, string> = {
  Overview: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  Threads:
    "M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5A8.5 8.5 0 0 1 10.5 3H21z M7 9h9 M7 14h6",
  Environments: "M3 5h18v14H3z M7 9l3 3-3 3 M13 15h4",
  Settings: "M3 6h18 M3 12h18 M3 18h18 M8 3v6 M16 9v6 M10 15v6",
};
function Icon({ page }: { page: Page }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[page]} />
    </svg>
  );
}
function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true">
        a<span>13</span>n
      </span>
      <span>
        Claw<small>CONSOLE</small>
      </span>
    </div>
  );
}
function App() {
  const [session, setSession] = useState<{
    api: Api;
    instance: Instance;
  } | null>(null);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (session)
    return (
      <CommandProvider>
        <Console
          api={session.api}
          initial={session.instance}
          logout={() => {
            setSession(null);
            setToken("");
          }}
        />
      </CommandProvider>
    );
  return (
    <main className="login">
      <Brand />
      <section className="login-card">
        <p className="eyebrow">YOUR INSTANCE. YOUR WORK.</p>
        <h1>Continue where you left off.</h1>
        <p>
          Connect to this self-hosted Claw instance to manage conversations,
          decisions and working environments.
        </p>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError("");
            try {
              const api = new Api(token.trim());
              const instance = await api.get<Instance>("/instance");
              setSession({ api, instance });
              setToken("");
            } catch (reason) {
              setError(message(reason));
            } finally {
              setBusy(false);
            }
          }}
        >
          <label>
            Access token
            <input
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoComplete="off"
              autoFocus
              required
            />
          </label>
          <ErrorNotice>{error}</ErrorNotice>
          <button className="primary" disabled={busy || !token.trim()}>
            {busy ? "Connecting…" : "Connect to Claw"}
          </button>
        </form>
        <details>
          <summary>First time here?</summary>
          <p>
            The server writes an operator token to <code>operator.token</code>{" "}
            inside its application data directory (by default{" "}
            <code>~/.a13n-claw</code>). Read it locally; never put it in a URL.
          </p>
          <p>
            Tokens are held only in this tab's memory. Use HTTPS when accessing
            an instance remotely.
          </p>
        </details>
      </section>
      <footer>Built on a13n Harness · self-hosted by design</footer>
    </main>
  );
}
function Console({
  api,
  initial,
  logout,
}: {
  api: Api;
  initial: Instance;
  logout: () => void;
}) {
  const instance = useRemote<Instance>(api, "/instance", 5000);
  const info = instance.data ?? initial;
  const threads = useRemote<Thread[]>(api, "/threads", 2000);
  const profiles = useRemote<Resource[]>(api, "/profiles", 5000);
  const [page, setPage] = useState<Page>("Overview");
  const [selected, setSelected] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [search, setSearch] = useState("");
  const [newThread, setNewThread] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const thread = threads.data?.find((item) => item.id === selected);
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [page, selected, newThread]);
  const pendingCommands = usePendingCommands();
  const hasDraft =
    pendingCommands ||
    Object.values(drafts).some((item) => item.text || item.files.length);
  useEffect(() => {
    if (!hasDraft) return;
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [hasDraft]);
  const navigate = (id: string) => {
    setSelected(id);
    setPage("Threads");
    setNewThread(false);
    threads.reload();
  };
  const refresh = () => {
    threads.reload();
    profiles.reload();
    instance.reload();
  };
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <Brand />
        <div className="nav-label">INSTANCE</div>
        <nav aria-label="Console">
          {(
            [
              "Overview",
              "Threads",
              "Environments",
              ...(info.principal.admin ? ["Settings"] : []),
            ] as Page[]
          ).map((item) => (
            <button
              key={item}
              aria-current={page === item ? "page" : undefined}
              onClick={() => setPage(item)}
            >
              <Icon page={item} />
              <span>{item}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <a
            href="https://a13n-claw.wh1isper.top/docs/"
            target="_blank"
            rel="noreferrer"
          >
            Documentation <span aria-hidden="true">↗</span>
          </a>
          <small>
            {info.principal.id} ·{" "}
            {info.principal.admin ? "Operator" : "Participant"}
          </small>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span>
            Console <span className="breadcrumb-divider">/</span>
            <strong>{page}</strong>
          </span>
          <span className="toolbar">
            <Status value={instance.error ? "disconnected" : info.dispatcher} />
            <small className="version">v{info.version}</small>
            <button
              onClick={() => {
                if (
                  hasDraft &&
                  !confirm(
                    "Disconnect and discard unsent drafts? Accepted work continues.",
                  )
                )
                  return;
                logout();
              }}
            >
              Disconnect
            </button>
          </span>
        </header>
        <main id="main" tabIndex={-1}>
          <ErrorNotice>
            {instance.error || threads.error || profiles.error}
          </ErrorNotice>
          <div hidden={page !== "Overview"}>
            <header className="page-heading">
              <div>
                <p className="eyebrow">LOCAL-FIRST · DURABLE WORK</p>
                <h1>Your work, with continuity.</h1>
                <p>Start a conversation. Keep control of what happens next.</p>
              </div>
              <button
                className="primary"
                disabled={!allowed(info.principal, "create")}
                onClick={() => {
                  setPage("Threads");
                  setNewThread(true);
                }}
              >
                New Thread
              </button>
            </header>
            <section className="welcome">
              <div>
                <p className="eyebrow">INSTANCE WORKSPACE</p>
                <h2>
                  One place for working files.
                  <br />
                  Independent histories.
                </h2>
                <p>
                  Threads share working data. Runs retain their own captured
                  definitions, decisions and saved outcomes.
                </p>
                {info.workspace && <code>{info.workspace}</code>}
              </div>
              <div className="overview-stats">
                <span>
                  <strong>
                    {threads.data?.filter((item) => !item.archived).length ??
                      "—"}
                  </strong>
                  Active Threads
                </span>
                <span>
                  <strong>{profiles.data?.length ?? "—"}</strong>Available
                  profiles
                </span>
              </div>
            </section>
            {!profiles.data?.length && info.principal.admin && (
              <section className="panel setup">
                <h2>Set up your first agent</h2>
                <p>
                  Create a model with a credential reference, choose a Local or
                  Docker environment, then connect them in a profile. Save
                  credentials separately. Finally select the default profile or
                  choose one when creating a Thread.
                </p>
                <button onClick={() => setPage("Settings")}>
                  Open settings
                </button>
              </section>
            )}
            <section className="panel">
              <div className="section-heading">
                <h2>Recent conversations</h2>
                <button onClick={() => setPage("Threads")}>All Threads</button>
              </div>
              {threads.data?.slice(0, 5).map((item) => (
                <button
                  key={item.id}
                  className="recent-thread"
                  onClick={() => navigate(item.id)}
                >
                  <span>
                    <strong>{item.title}</strong>
                    <small>
                      {item.profile_id} ·{" "}
                      {new Date(item.created_at).toLocaleDateString()}
                    </small>
                  </span>
                  <span aria-hidden="true">→</span>
                </button>
              ))}
              {threads.data?.length === 0 && (
                <p className="subtle">No saved conversations yet.</p>
              )}
            </section>
            <section className="panel">
              <h2>Readiness is specific.</h2>
              <p>
                Dispatcher: <strong>{info.dispatcher}</strong>. Provider
                connectivity:{" "}
                <strong>{info.connectivity.replaceAll("_", " ")}</strong>. A
                saved credential or a configured profile is not proof of a
                reachable model or MCP server.
              </p>
              {info.error && <ErrorNotice>{info.error}</ErrorNotice>}
            </section>
          </div>
          <div
            hidden={page !== "Threads" && page !== "Environments"}
            className="thread-layout"
          >
            <aside className="thread-picker">
              <div className="section-heading">
                <h2>Threads</h2>
                {allowed(info.principal, "create") && (
                  <button
                    aria-label="New Thread"
                    onClick={() => {
                      setNewThread(true);
                      setPage("Threads");
                    }}
                  >
                    +
                  </button>
                )}
              </div>
              <label className="sr-only" htmlFor="thread-search">
                Search Threads
              </label>
              <input
                id="thread-search"
                placeholder="Find a conversation"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={showArchived}
                  onChange={(event) => setShowArchived(event.target.checked)}
                />
                Show archived
              </label>
              <div className="thread-list">
                {threads.data
                  ?.filter(
                    (item) =>
                      (showArchived || !item.archived) &&
                      item.title.toLowerCase().includes(search.toLowerCase()),
                  )
                  .map((item) => (
                    <button
                      key={item.id}
                      className={selected === item.id ? "selected" : ""}
                      onClick={() => {
                        setSelected(item.id);
                        setNewThread(false);
                      }}
                    >
                      <strong>{item.title}</strong>
                      <small>
                        {item.profile_id}
                        {item.archived ? " · archived" : ""}
                      </small>
                    </button>
                  ))}
              </div>
            </aside>
            <div className="thread-content">
              <div hidden={page !== "Threads"}>
                {newThread ? (
                  <NewThread
                    api={api}
                    profiles={profiles.data ?? []}
                    done={navigate}
                    cancel={() => setNewThread(false)}
                  />
                ) : thread ? (
                  <Conversation
                    key={thread.id}
                    active={page === "Threads"}
                    api={api}
                    actor={info.principal}
                    thread={thread}
                    profiles={profiles.data ?? []}
                    draft={drafts[thread.id] ?? blankDraft()}
                    setDraft={(value) =>
                      setDrafts((previous) => ({
                        ...previous,
                        [thread.id]: value(previous[thread.id] ?? blankDraft()),
                      }))
                    }
                    changed={refresh}
                    navigate={navigate}
                  />
                ) : (
                  <Empty title="Choose a conversation.">
                    Select a Thread on the left or start a new one. Accepted
                    work continues even when you navigate away.
                  </Empty>
                )}
              </div>
              <div hidden={page !== "Environments"}>
                <Environments
                  key={thread?.id ?? "none"}
                  active={page === "Environments"}
                  api={api}
                  actor={info.principal}
                  thread={thread}
                />
              </div>
            </div>
          </div>
          {info.principal.admin && (
            <div hidden={page !== "Settings"}>
              <Settings
                api={api}
                changed={refresh}
                active={page === "Settings"}
              />
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
