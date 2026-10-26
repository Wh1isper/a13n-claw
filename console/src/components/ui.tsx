import { useMutation } from "@tanstack/react-query";
import { useId, useState, type ReactNode } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { Popover } from "@base-ui/react/popover";
import { ApiError } from "../api/client";
import { compact, exact } from "../format";
import { Icon, IconButton } from "./icons";

export function PageHeader({
  title,
  actions,
}: {
  title: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
      </div>
      <div className="actions">{actions}</div>
    </header>
  );
}
export function Panel({
  title,
  actions,
  children,
  className = "",
}: {
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <header className="panel-header">
          <h2>{title}</h2>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <div className="muted">{children}</div>}
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      Loading…
    </div>
  );
}
export function ErrorNotice({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  if (!error) return null;
  return (
    <div className="notice error" role="alert">
      <strong>
        {error instanceof Error ? error.message : "Request failed"}
      </strong>
      {error instanceof ApiError && error.fields.length > 0 && (
        <ul>
          {error.fields.map((field, i) => (
            <li key={i}>
              {field.path.slice(1).join(" → ")}:{" "}
              {field.code.replaceAll("_", " ")}
            </li>
          ))}
        </ul>
      )}
      {retry && <button onClick={retry}>Try again</button>}
    </div>
  );
}
export function Badge({ children }: { children: string }) {
  return (
    <span className={`badge state-${children}`}>
      {children.replaceAll("_", " ")}
    </span>
  );
}
export function NumberValue({ value }: { value: number }) {
  return (
    <Popover.Root>
      <Popover.Trigger
        className="number-value"
        aria-label={`${exact(value)}; show exact value`}
      >
        {compact(value)}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={8}>
          <Popover.Popup className="popover">
            {exact(value)}
            <Popover.Arrow />
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
export function Info({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <Popover.Root>
      <Popover.Trigger className="info-button" aria-label={label} title={label}>
        <Icon name="info" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={8}>
          <Popover.Popup className="popover">{children}</Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
export function Raw({
  value,
  title = "Diagnostic details",
}: {
  value: unknown;
  title?: string;
}) {
  return (
    <details className="raw">
      <summary>{title}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
export function Modal({
  title,
  description,
  children,
  close,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  close: () => void;
}) {
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="backdrop" />
        <Dialog.Popup className="dialog">
          <div className="panel-header">
            <Dialog.Title>{title}</Dialog.Title>
            <Dialog.Close aria-label="Close dialog" className="icon-button">
              <Icon name="close" />
            </Dialog.Close>
          </div>
          {description && (
            <Dialog.Description className="muted">
              {description}
            </Dialog.Description>
          )}
          {children}
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
export function Copy({ value }: { value: string }) {
  const [message, setMessage] = useState("");
  const id = useId();
  return (
    <span className="copy">
      <code>{value}</code>
      <IconButton
        icon={message === "Copied" ? "check" : "copy"}
        label="Copy"
        aria-describedby={id}
        onClick={() => {
          void navigator.clipboard.writeText(value).then(
            () => setMessage("Copied"),
            () => setMessage("Select and copy the text"),
          );
        }}
      />
      <span id={id} role="status" className="sr-only">
        {message}
      </span>
    </span>
  );
}

export function ConfirmAction({
  title,
  description,
  action,
  close,
}: {
  title: string;
  description: string;
  action: () => Promise<unknown>;
  close: () => void;
}) {
  const mutation = useMutation({ mutationFn: action, onSuccess: close });
  return (
    <Modal
      title={title}
      description={description}
      close={() => {
        if (!mutation.isPending) close();
      }}
    >
      <ErrorNotice error={mutation.error} />
      <div className="form-actions">
        <button disabled={mutation.isPending} onClick={close}>
          Cancel
        </button>
        <button
          className="danger"
          disabled={mutation.isPending}
          onClick={() => mutation.mutate()}
        >
          {mutation.isPending ? "Working…" : "Confirm"}
        </button>
      </div>
    </Modal>
  );
}
