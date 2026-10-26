import type { ComponentProps } from "react";

const paths = {
  overview: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
  runs: "m4 6 5 5-5 5m8 2h8",
  models: "m12 3-9 5 9 5 9-5-9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5",
  calendar:
    "M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z",
  refresh:
    "M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.6-2L20 8M4 16l2.3 3A7 7 0 0 0 17.9 17",
  plus: "M12 5v14M5 12h14",
  edit: "m16 3 5 5-12 12-6 1 1-6L16 3Zm-3 3 5 5",
  trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7",
  play: "m7 4 14 8-14 8V4Z",
  pause: "M7 4v16M17 4v16",
  close: "m6 6 12 12M6 18 18 6",
  copy: "M9 9h12v12H9zM15 5V3H3v12h2",
  check: "m5 12 4 4L19 6",
  left: "m14 6-6 6 6 6",
  right: "m10 6 6 6-6 6",
  up: "m6 14 6-6 6 6",
  down: "m6 10 6 6 6-6",
  search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  logout: "M9 4H4v16h5M13 7l5 5-5 5M8 12h13",
  sun: "M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  moon: "M21 13a9 9 0 1 1-10-10 7 7 0 0 0 10 10Z",
  monitor: "M3 3h18v14H3zM8 21h8m-4-4v4",
  info: "M12 11v6m0-10v.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  external: "M14 3h7v7m0-7L10 14M10 3H3v18h18v-7",
  key: "m14 10 7 7-3 3-2-2-2 2-4-6M13 7a6 6 0 1 1-12 0 6 6 0 0 1 12 0",
  archive: "M3 3h18v5H3zM5 8v13h14V8M10 12h4",
} as const;
export type IconName = keyof typeof paths;

export function Icon({
  name,
  className = "",
}: {
  name: IconName;
  className?: string;
}) {
  return (
    <svg
      className={`icon ${className}`}
      aria-hidden="true"
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={paths[name]} />
    </svg>
  );
}

export function IconButton({
  icon,
  label,
  busy,
  className = "",
  ...props
}: Omit<ComponentProps<"button">, "children"> & {
  icon: IconName;
  label: string;
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      {...props}
      className={`icon-button ${className}`}
      aria-label={label}
      title={label}
      aria-busy={busy || undefined}
    >
      <Icon name={icon} className={busy ? "spinning" : ""} />
    </button>
  );
}
