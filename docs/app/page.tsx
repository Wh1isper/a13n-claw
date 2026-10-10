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
          The foundation for a personal agent runtime. An independent project
          inspired by YA Claw, with a13n Harness as its execution foundation.
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
          authenticated agent execution, durable conversations, reusable
          environments, and a working Console. Published 0.0.2 artifacts remain
          the earlier CLI-only placeholder.
        </p>
      </section>
      <section className="cards" aria-label="Project principles">
        <article>
          <h2>One execution foundation</h2>
          <p>
            Agent execution uses Harness public APIs, rather than maintaining
            another agent engine.
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
