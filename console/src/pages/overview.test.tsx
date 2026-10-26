// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Trend } from "./overview";
import { NumberValue } from "../components/ui";
import {
  CopilotBuckets,
  GrokBilling,
  QuotaMeter,
} from "../components/allowances";
import type { Schema } from "../api/client";

afterEach(cleanup);
const totals: Schema["ConsumptionTotals"] = {
  total_tokens: 2345678901,
  input_tokens: 2000000000,
  output_tokens: 345678901,
  cache_read_tokens: 400,
  cache_write_tokens: 0,
  model_requests: 3,
  model_cost_usd: null,
  unknown_model_costs: 3,
};
it("makes exact daily values available by click and keyboard", async () => {
  const user = userEvent.setup();
  render(<Trend days={[{ day: "2026-10-26", totals }]} metric="tokens" />);
  const day = screen.getByRole("button", {
    name: "2026-10-26: 2,345,678,901 tokens",
  });
  day.focus();
  await user.keyboard("{Enter}");
  expect(
    screen.getByText("2026-10-26 · 2,345,678,901 tokens · — (partial cost)"),
  ).toBeDefined();
});
it("does not turn unknown daily cost into measured zero", () => {
  render(<Trend days={[{ day: "2026-10-26", totals }]} metric="cost" />);
  expect(
    screen.getByRole("button", { name: "2026-10-26: —, partial cost" }),
  ).toBeDefined();
});
it("exposes compact metric exact values to touch and assistive technology", async () => {
  const user = userEvent.setup();
  render(<NumberValue value={2345678901} />);
  await user.click(
    screen.getByRole("button", { name: "2,345,678,901; show exact value" }),
  );
  expect(await screen.findByText("2,345,678,901")).toBeDefined();
});

it("preserves over-limit percentages while bounding the visual meter", () => {
  render(<QuotaMeter label="Weekly allowance" used={123.5} />);
  const meter = screen.getByRole("meter", {
    name: "Weekly allowance: 123.5% used",
  });
  expect(meter.getAttribute("value")).toBe("100");
  expect(screen.getByText("123.5% used")).toBeDefined();
});
it("converts Copilot remaining percentage without confusing unlimited or missing buckets", () => {
  render(
    <CopilotBuckets
      usage={{
        quota_snapshots: {
          premium: {
            unlimited: false,
            percent_remaining: 25,
            remaining: 75,
            entitlement: 300,
          },
          chat: { unlimited: true },
          completions: { unlimited: false },
        },
      }}
    />,
  );
  expect(
    screen.getByRole("meter", { name: "premium: 75% used" }),
  ).toBeDefined();
  expect(screen.getByText("chat · Unlimited")).toBeDefined();
  expect(screen.getByText("completions · Allowance unavailable")).toBeDefined();
  expect(screen.getAllByRole("meter")).toHaveLength(1);
});
it("uses Grok provider percentage and converts purchased credit cents", () => {
  render(
    <GrokBilling
      billing={{
        credit_usage_percent: 30,
        used: { val: 900 },
        monthly_limit: { val: 1000 },
        prepaid_balance: { val: -1250 },
        is_unified_billing_user: true,
      }}
    />,
  );
  expect(
    screen.getByRole("meter", { name: "Current billing period: 30% used" }),
  ).toBeDefined();
  expect(screen.getByText("$12.50")).toBeDefined();
  expect(screen.getByText("Shared usage pool")).toBeDefined();
});
it("does not invent Grok allowance from a missing billing snapshot", () => {
  render(<GrokBilling billing={{}} />);
  expect(screen.getByText("Numeric allowance unavailable")).toBeDefined();
  expect(screen.queryByRole("meter")).toBeNull();
});
