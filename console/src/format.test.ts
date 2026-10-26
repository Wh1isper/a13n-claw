import { describe, expect, it } from "vitest";
import { compact, dateRange, duration, exact, money } from "./format";

describe("consumption formatting", () => {
  it("uses compact English units and keeps exact values", () => {
    expect(compact(0)).toBe("0");
    expect(compact(1_234)).toBe("1.23K");
    expect(compact(999_999)).toBe("1M");
    expect(compact(2_345_678_901)).toBe("2.35B");
    expect(exact(2_345_678_901)).toBe("2,345,678,901");
  });
  it("distinguishes absent, zero and tiny cost", () => {
    expect(money(null)).toBe("—");
    expect(money("0")).toBe("$0.00");
    expect(money("0.0000001")).toBe("<$0.0001");
    expect(money("12.3456")).toBe("$12.3456");
  });
  it("uses UTC dates with an exclusive end across month boundaries", () => {
    expect(dateRange(7, new Date("2026-11-01T00:30:00+08:00"))).toEqual({
      start: "2026-10-25",
      end: "2026-11-01",
    });
    expect(dateRange(1, new Date("2026-10-31T23:59:00Z"))).toEqual({
      start: "2026-10-31",
      end: "2026-11-01",
    });
  });
  it("derives quota window names from provider durations", () => {
    expect(duration(18000)).toBe("5 hour");
    expect(duration(604800)).toBe("7 day");
    expect(duration(1800)).toBe("30 minute");
  });
});
