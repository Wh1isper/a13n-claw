import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Page = "Overview" | "Threads" | "Environments" | "Settings";
const pages: Page[] = ["Overview", "Threads", "Environments", "Settings"];
const paths: Record<Page, string> = {
  Overview: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  Threads:
    "M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5A8.5 8.5 0 0 1 10.5 3H21z M7 9h9 M7 14h6",
  Environments: "M3 5h18v14H3z M7 9l3 3-3 3 M13 15h4",
  Settings: "M3 6h18 M3 12h18 M3 18h18 M8 3v6 M16 9v6 M10 15v6",
};
const details: Record<
  Exclude<Page, "Overview">,
  { title: string; description: string; planned: string[] }
> = {
  Threads: {
    title: "A place for work that continues.",
    description:
      "Conversations, execution history, and decisions will live here. Thread creation and agent execution are not available in this preview.",
    planned: [
      "Persistent conversations",
      "Run history and pending decisions",
      "Continuation and recovery",
    ],
  },
  Environments: {
    title: "Your work. In its own environment.",
    description:
      "Working contexts and managed execution environments will live here. This preview does not inspect your filesystem or connect to Docker.",
    planned: [
      "Workspace selection",
      "Managed execution environments",
      "Scoped files and outputs",
    ],
  },
  Settings: {
    title: "An explicit home for configuration.",
    description:
      "Models, agents, and instance configuration will live here. This preview does not read credentials, save settings, or configure a runtime.",
    planned: [
      "Model and agent definitions",
      "Instance access controls",
      "Bridge and automation configuration",
    ],
  },
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

function App() {
  const [page, setPage] = useState<Page>("Overview");
  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            setPage("Overview");
          }}
          aria-label="a13n Claw overview"
        >
          <span className="brand-mark" aria-hidden="true">
            a<span>13</span>n
          </span>
          <span>
            Claw <small>CONSOLE</small>
          </span>
        </a>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="Console">
          {pages.map((item) => (
            <button
              key={item}
              aria-current={page === item ? "page" : undefined}
              onClick={() => setPage(item)}
            >
              <Icon page={item} />
              <span>{item}</span>
              {item !== "Overview" && (
                <span className="nav-preview">Preview</span>
              )}
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
          <a
            href="https://github.com/Wh1isper/a13n-claw"
            target="_blank"
            rel="noreferrer"
          >
            Source code <span aria-hidden="true">↗</span>
          </a>
          <div className="foundation">
            Built on <strong>a13n Harness</strong>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span>
            Console <span className="breadcrumb-divider">/</span>{" "}
            <strong>{page}</strong>
          </span>
          <span className="preview-badge">Interface preview</span>
        </header>
        <main id="main" tabIndex={-1}>
          <div className="page-heading">
            <div>
              <p className="eyebrow">A13N CLAW</p>
              <h1>{page}</h1>
            </div>
            <span className="stage-label">EARLY DEVELOPMENT</span>
          </div>
          <div className="notice">
            <span className="notice-mark" aria-hidden="true">
              i
            </span>
            <p>
              <strong>A console, not a running agent.</strong> This is an
              interface placeholder. Execution, persistence, and authentication
              are not implemented.
            </p>
          </div>
          {page === "Overview" ? (
            <>
              <section className="welcome">
                <div className="welcome-copy">
                  <p className="eyebrow">LOCAL-FIRST · SELF-HOSTED</p>
                  <h2>
                    A home for your
                    <br />
                    agent work.
                  </h2>
                  <p>
                    Persistent conversations, explicit control, and work that
                    can continue. A small starting point for a runtime built on
                    a13n Harness.
                  </p>
                  <a
                    className="primary-link"
                    href="https://a13n-claw.wh1isper.top/docs/"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Explore the documentation <span aria-hidden="true">↗</span>
                  </a>
                </div>
                <div className="blueprint" aria-hidden="true">
                  <div className="blueprint-orbit">
                    <div className="blueprint-core">
                      a13n<span>Claw</span>
                    </div>
                  </div>
                  <span className="blueprint-label label-one">CONTEXT</span>
                  <span className="blueprint-label label-two">CONTINUITY</span>
                  <span className="blueprint-caption">
                    A foundation for what comes next
                  </span>
                </div>
              </section>
              <section aria-labelledby="planned-heading">
                <div className="section-heading">
                  <h2 id="planned-heading">A look ahead</h2>
                  <span>Planned capabilities</span>
                </div>
                <div className="feature-grid">
                  {(["Threads", "Environments", "Settings"] as const).map(
                    (item) => (
                      <button
                        className="feature-card"
                        key={item}
                        onClick={() => setPage(item)}
                      >
                        <span className="feature-icon">
                          <Icon page={item} />
                        </span>
                        <h3>
                          {item}
                          <span aria-hidden="true">→</span>
                        </h3>
                        <p>
                          {details[item].planned[0]}. {details[item].planned[1]}
                          .
                        </p>
                        <span className="card-status">Not implemented</span>
                      </button>
                    ),
                  )}
                </div>
              </section>
              <section className="boundary">
                <div>
                  <h2>Small surface. Clear boundaries.</h2>
                  <p>
                    This build serves static console assets only. There is no
                    agent backend connected to this interface.
                  </p>
                </div>
                <code>a13n-claw serve</code>
              </section>
            </>
          ) : (
            <section className="empty-state" aria-labelledby="empty-heading">
              <span className="empty-icon">
                <Icon page={page} />
              </span>
              <span className="card-status">Not implemented</span>
              <h2 id="empty-heading">{details[page].title}</h2>
              <p>{details[page].description}</p>
              <ul>
                {details[page].planned.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              <button
                className="secondary-button"
                onClick={() => setPage("Overview")}
              >
                Back to overview <span aria-hidden="true">→</span>
              </button>
            </section>
          )}
          <footer>
            Self-hosted by design.<span>Preview only · No runtime state</span>
          </footer>
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
