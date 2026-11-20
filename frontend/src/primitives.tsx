import {
  Badge,
  Button,
  Empty,
  ErrorPage,
  Input,
  Link,
  Loading,
  React,
  Timestamp,
  useAccess,
  useApp,
} from "@a13n/console";
import {
  appPath,
  clawPath,
  threadPath,
  url,
  useRead,
  useSelection,
  type Claw,
  type Collection,
  type Overview,
} from "./data";
import styles from "./app.module.css";

export function Icon({
  name,
}: {
  name: "open" | "inbox" | "settings" | "next" | "back" | "plus";
}) {
  return (
    <svg
      aria-hidden="true"
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {name === "open" ? (
        <path d="M7 17 17 7M7 7h10v10" />
      ) : name === "inbox" ? (
        <path d="M4 13l3-7h10l3 7v5H4Zm0 0h5l1 2h4l1-2h5" />
      ) : name === "plus" ? (
        <path d="M6 12h12M12 6v12" />
      ) : name === "next" ? (
        <path d="m9 6 6 6-6 6" />
      ) : name === "back" ? (
        <path d="m15 6-6 6 6 6" />
      ) : (
        <path d="M4 7h16M4 17h16M9 4v6m6 4v6" />
      )}
    </svg>
  );
}
export function Status({ state }: { state: string }) {
  const label = state === "full" ? "Inbox full" : state.replaceAll("_", " ");
  const variant = ["failed", "full", "dropped", "cancelled"].includes(state)
    ? "error"
    : [
          "completed",
          "enabled",
          "accepted",
          "read",
          "ready",
          "verified",
        ].includes(state)
      ? "success"
      : [
            "waiting",
            "unread",
            "processing",
            "not verified",
            "not installed",
            "unavailable",
            "missing",
          ].includes(state)
        ? "warning"
        : "secondary";
  return (
    <Badge variant={variant}>
      <span className={styles.dot} />
      {label}
    </Badge>
  );
}
export function DateTime({ value }: { value: string | null }) {
  return value ? (
    <Timestamp value={value} />
  ) : (
    <span className={styles.muted}>—</span>
  );
}
export function ThreadLink({
  session,
  thread,
  run,
  children,
}: {
  session: string | null;
  thread: string | null;
  run?: string | null;
  children: React.ReactNode;
}) {
  const { workspaceId } = useApp();
  if (!session || !thread || !workspaceId) return null;
  return (
    <Link
      className={styles.link}
      to={threadPath(workspaceId, session, thread, run)}
    >
      {children}
      <Icon name="open" />
    </Link>
  );
}
export function ClawLink({
  id,
  page,
  children,
}: {
  id?: string;
  page: string;
  children: React.ReactNode;
}) {
  const { workspaceId } = useApp();
  return (
    <Link
      className={styles.link}
      to={url(appPath(workspaceId!, page), { claw: id })}
    >
      {children}
    </Link>
  );
}
export function ResourceLink({
  path,
  children,
}: {
  path: string;
  children: React.ReactNode;
}) {
  const { workspaceId } = useApp();
  return (
    <Link className={styles.link} to={`/workspace/${workspaceId}/${path}`}>
      {children}
      <Icon name="open" />
    </Link>
  );
}
export function ClawScope({
  children,
  setup,
}: {
  children: (claw: Claw, picker: React.ReactNode) => React.ReactNode;
  setup?: React.ReactNode;
}) {
  const { params, select } = useSelection();
  const { can } = useAccess();
  const [pages, setPages] = React.useState<string[]>([]);
  const claws = useRead<Collection<Claw>>(
    url("/claws", { limit: "50", cursor: pages.at(-1) }),
  );
  const wanted = params.get("claw");
  const listed = claws.data?.items.find(({ id }) => id === wanted);
  const chosen = useRead<Overview>(wanted && !listed ? clawPath(wanted) : null);
  const pick = (claw: string | null) =>
    select({ claw, delivery: null, connector: null, schedule: null });
  if (claws.isPending || (wanted && !listed && chosen.isPending))
    return <Loading page />;
  if (claws.isError) return <ErrorPage error={claws.error} />;
  const claw = wanted ? (listed ?? chosen.data?.claw) : claws.data.items[0];
  if (!claw)
    return wanted ? (
      <Empty
        title="Claw unavailable"
        description={
          chosen.error?.message ?? "Choose a Claw in this workspace."
        }
        action={
          <Button variant="outline" onClick={() => pick(null)}>
            Choose a Claw
          </Button>
        }
      />
    ) : (
      (setup ?? (
        <Empty
          icon={<Icon name="inbox" />}
          title="No Claws in this workspace"
          description="A Claw brings incoming work into one persistent Main Thread."
          action={
            can("admin") && (
              <ClawLink page="settings">
                Set up Claw <Icon name="open" />
              </ClawLink>
            )
          }
        />
      ))
    );
  const options =
    listed || !wanted ? claws.data.items : [claw, ...claws.data.items];
  return children(
    claw,
    <div className={styles.actions}>
      <select
        aria-label="Claw"
        className={styles.select}
        value={claw.id}
        onChange={(event) => pick(event.target.value)}
      >
        {options.map((item) => (
          <option key={item.id} value={item.id}>
            {item.name}
          </option>
        ))}
      </select>
      {(pages.length > 0 || claws.data.next_cursor) && (
        <Pager
          next={!!claws.data.next_cursor}
          onNext={() => {
            if (!wanted) pick(claw.id);
            setPages([...pages, claws.data.next_cursor!]);
          }}
          onPrevious={
            pages.length ? () => setPages(pages.slice(0, -1)) : undefined
          }
        />
      )}
    </div>,
  );
}
export function Pager({
  next,
  onNext,
  onPrevious,
}: {
  next: boolean;
  onNext: () => void;
  onPrevious?: () => void;
}) {
  return (
    <div className={styles.pager}>
      <Button
        type="button"
        variant="ghost"
        disabled={!onPrevious}
        onClick={onPrevious}
        aria-label="Previous page"
      >
        <Icon name="back" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        disabled={!next}
        onClick={onNext}
        aria-label="Next page"
      >
        <Icon name="next" />
      </Button>
    </div>
  );
}
export function InputValue({
  name,
  value,
  maxLength,
  required = true,
}: {
  name: string;
  value?: string | number | null;
  maxLength?: number;
  required?: boolean;
}) {
  return (
    <Input
      name={name}
      defaultValue={value ?? ""}
      maxLength={maxLength}
      required={required}
    />
  );
}
