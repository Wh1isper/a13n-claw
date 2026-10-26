import Link from "next/link";
export default function Home() {
  return (
    <main className="landing">
      <nav className="topbar" aria-label="Main navigation">
        <Link href="/" className="brand">
          a13n / claw
        </Link>
        <a href="https://github.com/Wh1isper/a13n-claw">GitHub ↗</a>
      </nav>
      <section className="hero">
        <p className="eyebrow">Local-first · Harness-native · Open source</p>
        <h1>
          A fresh start.
          <br />
          Built around Harness.
        </h1>
        <p className="lede">
          An IM-first assistant embedding Harness UI. An independent project
          inspired by YA Claw, with native execution, collaboration, and memory.
        </p>
        <div className="actions">
          <Link href="/docs/" className="primary">
            Read the documentation
          </Link>
          <a href="https://github.com/Wh1isper/a13n-claw">
            Explore the repository ↗
          </a>
        </div>
        <p className="notice">
          <strong>Real work, retained context.</strong> Source builds include
          Lark and GitHub interaction, isolated schedules, persistent Docker
          execution, and headless CLI/API controls. The runtime page is Hello
          World. Published 0.0.2 artifacts remain the earlier CLI-only
          placeholder.
        </p>
      </section>
      <section className="cards" aria-label="Project principles">
        <article>
          <h2>One execution foundation</h2>
          <p>
            One embedded Harness UI App owns execution, Threads, collaboration,
            and memory through its public library APIs.
          </p>
        </article>
        <article>
          <h2>A clean boundary</h2>
          <p>
            A new project with its own configuration and lifecycle. No legacy YA
            configuration compatibility.
          </p>
        </article>
        <article>
          <h2>Ready to build on</h2>
          <p>
            Reproducible tools, local integration tests, documented contracts,
            and versioned package and image publication.
          </p>
        </article>
      </section>
      <footer>BSD-3-Clause · Maintained by Wh1isper</footer>
    </main>
  );
}
