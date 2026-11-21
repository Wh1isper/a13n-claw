import { React } from "@a13n/console";

export type IconName =
  | "claw"
  | "inbox"
  | "activity"
  | "clock"
  | "plug"
  | "model"
  | "terminal"
  | "browser"
  | "memory"
  | "settings"
  | "external"
  | "next"
  | "copy"
  | "check"
  | "plus"
  | "refresh"
  | "error"
  | "warning"
  | "info"
  | "archive";
const paths: Record<IconName, React.ReactNode> = {
  claw: (
    <>
      <rect x="4" y="7" width="16" height="12" rx="3" />
      <path d="M12 3v4M9 13h.01M15 13h.01M9 16h6" />
    </>
  ),
  inbox: <path d="M4 5h16l2 9v5H2v-5zm-2 9h6l2 2h4l2-2h6" />,
  activity: <path d="M2 12h5l3-7 4 14 3-7h5" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  plug: <path d="M7 3v6m6-6v6M5 9h10v2a5 5 0 0 1-5 5v5m7-12h2" />,
  model: (
    <>
      <rect x="6" y="6" width="12" height="12" rx="2" />
      <path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4" />
    </>
  ),
  terminal: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="m7 9 3 3-3 3m5 0h5" />
    </>
  ),
  browser: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 8h18M7 6h.01" />
    </>
  ),
  memory: (
    <>
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v7c0 2 16 2 16 0V5M4 12v7c0 2 16 2 16 0v-7" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M19 5l-2 2M7 17l-2 2" />
    </>
  ),
  external: <path d="M7 17 17 7M9 7h8v8" />,
  next: <path d="m9 6 6 6-6 6" />,
  copy: (
    <>
      <rect x="8" y="8" width="11" height="12" rx="2" />
      <path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h3" />
    </>
  ),
  check: <path d="m5 12 4 4L19 6" />,
  plus: <path d="M12 5v14M5 12h14" />,
  refresh: (
    <path d="M20 8a8 8 0 0 0-14-2L3 9m0-5v5h5M4 16a8 8 0 0 0 14 2l3-3m0 5v-5h-5" />
  ),
  error: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6m0 3h.01" />
    </>
  ),
  warning: (
    <>
      <path d="M12 3 2 20h20L12 3z" />
      <path d="M12 9v4m0 3h.01" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5m0-8h.01" />
    </>
  ),
  archive: (
    <>
      <path d="M4 7h16v13H4zM3 4h18v3H3zm6 7h6" />
    </>
  ),
};
export function Icon({ name, size = 15 }: { name: IconName; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.65}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}
