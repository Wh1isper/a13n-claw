const integer = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const short = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 2,
});
export const exact = (value: number) => integer.format(value);
export const compact = (value: number) => short.format(value);
export function money(value: string | null | undefined) {
  if (value == null) return "—";
  const number = Number(value);
  if (number > 0 && number < 0.0001) return "<$0.0001";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  }).format(number);
}
export function timestamp(value: string | number | null | undefined) {
  if (value == null || value === 0) return "—";
  return new Date(
    typeof value === "number" ? value * 1000 : value,
  ).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
export function duration(seconds: number) {
  if (seconds % 86400 === 0) return `${seconds / 86400} day`;
  if (seconds % 3600 === 0) return `${seconds / 3600} hour`;
  return `${Math.round(seconds / 60)} minute`;
}
export function dateRange(days: number, now = new Date()) {
  const end = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1),
  );
  return {
    start: new Date(end.getTime() - days * 86400000).toISOString().slice(0, 10),
    end: end.toISOString().slice(0, 10),
  };
}
