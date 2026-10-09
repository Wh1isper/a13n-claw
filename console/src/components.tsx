import { useState, type ReactNode } from "react";
import { message, pretty } from "./api";

export function ErrorNotice({ children }: { children?: string }) {
  return children ? (
    <div className="notice error" role="alert">
      {children}
    </div>
  ) : null;
}
export function Status({ value }: { value: string }) {
  return (
    <span className={`status status-${value}`}>
      {value.replaceAll("_", " ")}
    </span>
  );
}
export function JsonDetails({
  title,
  value,
}: {
  title: string;
  value: unknown;
}) {
  return (
    <details>
      <summary>{title}</summary>
      <pre>{pretty(value)}</pre>
    </details>
  );
}
export function Action({
  children,
  run,
  danger = false,
  disabled = false,
}: {
  children: ReactNode;
  run: () => Promise<unknown>;
  danger?: boolean;
  disabled?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <span className="action">
      <button
        className={danger ? "danger" : ""}
        disabled={busy || disabled}
        onClick={async () => {
          setBusy(true);
          setError("");
          try {
            await run();
          } catch (reason) {
            setError(message(reason));
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Working…" : children}
      </button>
      <ErrorNotice>{error}</ErrorNotice>
    </span>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="empty-state">
      <span className="eyebrow">A13N CLAW</span>
      <h2>{title}</h2>
      <p>{children}</p>
    </section>
  );
}
