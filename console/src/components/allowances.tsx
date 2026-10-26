import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { data, type Schema } from "../api/client";
import { useApi } from "../api/session";
import { Icon, IconButton } from "./icons";
import { Badge, Info, ErrorNotice, Loading, Panel } from "./ui";
import { duration, timestamp } from "../format";
import styles from "./allowances.module.css";

export function Allowances() {
  const api = useApi();
  const subscriptions = useQuery({
    queryKey: ["subscriptions"],
    queryFn: ({ signal }) => data(api.GET("/api/subscriptions", { signal })),
    refetchInterval: 60_000,
  });
  return (
    <section className="stack">
      <header className="page-header">
        <div>
          <div className="toolbar">
            <h2>Account allowance</h2>
            <Info label="Allowance details">
              Provider totals include activity outside Claw.
            </Info>
          </div>
        </div>
        <Link
          className="icon-button button"
          aria-label="Manage accounts"
          title="Manage accounts"
          to="/models?tab=accounts"
        >
          <Icon name="right" />
        </Link>
      </header>
      <ErrorNotice
        error={subscriptions.error}
        retry={() => void subscriptions.refetch()}
      />
      {subscriptions.isPending ? (
        <Loading />
      ) : (
        <div className="cards">
          {subscriptions.data?.accounts.map((account) => (
            <ManagedAllowance key={account.account.id} view={account} />
          ))}
          <SelectedAllowance provider="copilot" />
          <SelectedAllowance provider="chatgpt" />
        </div>
      )}
    </section>
  );
}
export function QuotaMeter({
  label,
  used,
  reset,
}: {
  label: string;
  used: number;
  reset?: string | number | null;
}) {
  return (
    <div className={styles.quota}>
      <div className={styles.label}>
        <span>{label}</span>
        <strong>
          {used.toLocaleString(undefined, { maximumFractionDigits: 2 })}% used
        </strong>
      </div>
      <meter
        min={0}
        max={100}
        value={Math.max(0, Math.min(100, used))}
        aria-label={`${label}: ${used}% used`}
      />
      {reset && (
        <p className="hint">
          Resets {timestamp(reset)}
          {new Date(
            typeof reset === "number" ? reset * 1000 : reset,
          ).getTime() < Date.now()
            ? " · refresh to confirm"
            : ""}
        </p>
      )}
    </div>
  );
}
function Windows({
  limits,
}: {
  limits: Schema["UsageLimits"] | null | undefined;
}) {
  return (
    <>
      {[limits?.primary_window, limits?.secondary_window].map(
        (window, index) =>
          window && (
            <QuotaMeter
              key={index}
              label={`${duration(window.limit_window_seconds)} window`}
              used={window.used_percent}
              reset={window.reset_at}
            />
          ),
      )}
    </>
  );
}
export function ManagedAllowance({
  view,
  actions,
}: {
  view: Schema["SubscriptionAccountView"];
  actions?: React.ReactNode;
}) {
  const api = useApi();
  const cache = useQueryClient();
  const { account, authentication, usage } = view;
  const refresh = useMutation({
    mutationFn: () =>
      data(
        api.POST("/api/subscriptions/accounts/{account_id}/refresh", {
          params: { path: { account_id: account.id } },
        }),
      ),
    onSuccess: () => cache.invalidateQueries({ queryKey: ["subscriptions"] }),
  });
  const snapshot = usage.usage;
  return (
    <Panel
      title={account.name}
      actions={
        <Badge>
          {!account.enabled
            ? "disabled"
            : authentication.usable
              ? "signed_in"
              : "sign_in_required"}
        </Badge>
      }
    >
      {(snapshot || authentication.usable || actions) && (
        <div className={`panel-body ${styles.body}`}>
          <p className="eyebrow">{account.provider}</p>
          {!snapshot ? (
            authentication.usable && (
              <p className="muted">Allowance unavailable</p>
            )
          ) : "rate_limit" in snapshot ? (
            <>
              <p className="hint">{snapshot.plan_type}</p>
              <Windows limits={snapshot.rate_limit} />
              {snapshot.additional_rate_limits?.map((extra, i) => (
                <div key={i}>
                  <p className="hint">{extra.limit_name}</p>
                  <Windows limits={extra.rate_limit} />
                </div>
              ))}
              {snapshot.credits && (
                <p className="hint">
                  Paid credits:{" "}
                  {snapshot.credits.unlimited
                    ? "Unlimited"
                    : (snapshot.credits.balance ?? "Unavailable")}
                </p>
              )}
              {snapshot.rate_limit_reset_credits && (
                <p className="hint">
                  {snapshot.rate_limit_reset_credits.available_count} reset
                  vouchers available
                </p>
              )}
              {snapshot.spend_control && (
                <div className="stack">
                  <p className="hint">
                    Spend control:{" "}
                    {snapshot.spend_control.reached
                      ? "Limit reached"
                      : "Within limit"}
                  </p>
                  {snapshot.spend_control.individual_limit && (
                    <>
                      <QuotaMeter
                        label="Individual spending limit"
                        used={
                          snapshot.spend_control.individual_limit.used_percent
                        }
                        reset={snapshot.spend_control.individual_limit.reset_at}
                      />
                      <p className="hint">
                        {snapshot.spend_control.individual_limit.used} used /{" "}
                        {snapshot.spend_control.individual_limit.limit} limit ·{" "}
                        {snapshot.spend_control.individual_limit.remaining}{" "}
                        remaining
                      </p>
                    </>
                  )}
                </div>
              )}
            </>
          ) : "config" in snapshot && snapshot.config ? (
            <>
              <GrokBilling billing={snapshot.config} />
            </>
          ) : (
            <p className="muted">Allowance unavailable</p>
          )}
          {usage.error && authentication.usable && (
            <p className="notice error">
              {snapshot
                ? "Refresh failed · last known allowance"
                : "Refresh failed"}
            </p>
          )}
          <ErrorNotice error={refresh.error} />
          {authentication.usable && (
            <div className={styles.footer}>
              <span className="hint">
                {usage.observed_at ? timestamp(usage.observed_at) : ""}
              </span>
              <IconButton
                icon="refresh"
                label="Refresh"
                busy={refresh.isPending}
                disabled={!authentication.usable || refresh.isPending}
                onClick={() => refresh.mutate()}
              />
            </div>
          )}
          {actions}
        </div>
      )}
    </Panel>
  );
}
export function SelectedAllowance({
  provider,
  actions,
}: {
  provider: "copilot" | "chatgpt";
  actions?: React.ReactNode;
}) {
  const api = useApi();
  const account = useQuery({
    queryKey: ["account", provider],
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/accounts/{provider}", {
          signal,
          params: { path: { provider } },
        }),
      ),
  });
  const quota = useQuery({
    queryKey: [
      "copilot-usage",
      account.data?.source,
      account.data?.source_id,
      account.data?.account_id,
    ],
    queryFn: ({ signal }) =>
      data(api.GET("/api/accounts/copilot/usage", { signal })),
    enabled: provider === "copilot" && account.data?.usable === true,
    staleTime: 60_000,
  });
  return (
    <Panel
      title={provider === "copilot" ? "GitHub Copilot" : "ChatGPT"}
      actions={
        account.data && (
          <Badge>
            {account.data.usable ? "signed_in" : "sign_in_required"}
          </Badge>
        )
      }
    >
      {(account.isPending ||
        account.error ||
        account.data?.usable ||
        actions) && (
        <div className={`panel-body ${styles.body}`}>
          <ErrorNotice error={account.error} />
          {account.isPending ? (
            <Loading />
          ) : (
            <>
              {account.data?.account_id && (
                <p className="hint">{account.data.account_id}</p>
              )}
              {account.data?.usable && provider === "chatgpt" ? (
                <p className="muted">Allowance unavailable</p>
              ) : account.data?.usable ? (
                <>
                  <ErrorNotice
                    error={quota.error}
                    retry={() => void quota.refetch()}
                  />
                  {quota.isPending ? (
                    <Loading />
                  ) : (
                    quota.data && (
                      <>
                        <p className="hint">
                          {quota.data.copilot_plan ?? "Current plan"}
                        </p>
                        <CopilotBuckets usage={quota.data} />
                        <p className="hint">
                          Observed {timestamp(quota.dataUpdatedAt / 1000)}
                        </p>
                        <IconButton
                          icon="refresh"
                          label="Refresh allowance"
                          busy={quota.isFetching}
                          disabled={quota.isFetching}
                          onClick={() => void quota.refetch()}
                        />
                      </>
                    )
                  )}
                </>
              ) : null}
            </>
          )}
          {actions}
        </div>
      )}
    </Panel>
  );
}

function dollars(cents: number | undefined) {
  return cents == null
    ? "Unavailable"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
      }).format(cents / 100);
}
export function GrokBilling({ billing }: { billing: Schema["GrokBilling"] }) {
  const used =
    billing.credit_usage_percent ??
    (billing.monthly_limit?.val &&
    billing.monthly_limit.val > 0 &&
    billing.used?.val != null
      ? (billing.used.val / billing.monthly_limit.val) * 100
      : null);
  const reset = billing.current_period?.end ?? billing.billing_period_end;
  const label =
    billing.current_period?.type === "USAGE_PERIOD_TYPE_WEEKLY"
      ? "Weekly allowance"
      : billing.current_period?.type === "USAGE_PERIOD_TYPE_MONTHLY"
        ? "Monthly allowance"
        : "Current billing period";
  return (
    <>
      {used != null ? (
        <QuotaMeter label={label} used={used} reset={reset} />
      ) : (
        <p className="hint">
          Numeric allowance unavailable
          {reset ? ` · resets ${timestamp(reset)}` : ""}
        </p>
      )}
      <dl className="facts">
        {billing.is_unified_billing_user && (
          <>
            <dt>Billing</dt>
            <dd>Shared usage pool</dd>
          </>
        )}
        {billing.prepaid_balance && (
          <>
            <dt>Purchased credits</dt>
            <dd>
              {dollars(
                billing.prepaid_balance.val == null
                  ? undefined
                  : Math.abs(billing.prepaid_balance.val),
              )}
            </dd>
          </>
        )}
        {billing.is_unified_billing_user === false && billing.on_demand_cap && (
          <>
            <dt>Pay as you go</dt>
            <dd>
              {billing.on_demand_cap.val === 0
                ? "Disabled"
                : `${dollars(billing.on_demand_used?.val)} used / ${dollars(billing.on_demand_cap.val)} cap`}
            </dd>
          </>
        )}
        {billing.monthly_limit && (
          <>
            <dt>Monthly limit</dt>
            <dd>{dollars(billing.monthly_limit.val)}</dd>
          </>
        )}
        {billing.used && (
          <>
            <dt>Reported spend</dt>
            <dd>{dollars(billing.used.val)}</dd>
          </>
        )}
      </dl>
    </>
  );
}
export function CopilotBuckets({ usage }: { usage: Schema["CopilotUsage"] }) {
  const buckets = Object.entries(usage.quota_snapshots ?? {});
  return (
    <div className="stack">
      {!buckets.length && (
        <p className="muted">Numeric allowance unavailable.</p>
      )}
      {buckets.map(([key, bucket]) => (
        <div key={key} className="stack">
          {bucket.unlimited ? (
            <p>{key.replaceAll("_", " ")} · Unlimited</p>
          ) : bucket.percent_remaining != null ? (
            <QuotaMeter
              label={key.replaceAll("_", " ")}
              used={100 - bucket.percent_remaining}
            />
          ) : (
            <p>{key.replaceAll("_", " ")} · Allowance unavailable</p>
          )}
          {!bucket.unlimited && bucket.remaining != null && (
            <p className="hint">
              {bucket.remaining.toLocaleString()} remaining
              {bucket.entitlement != null
                ? ` / ${bucket.entitlement.toLocaleString()} included`
                : ""}
            </p>
          )}
          {bucket.overage_permitted != null && (
            <p className="hint">
              Additional usage{" "}
              {bucket.overage_permitted ? "enabled" : "disabled"}
              {bucket.overage_count != null
                ? ` · ${bucket.overage_count.toLocaleString()} used`
                : ""}
            </p>
          )}
        </div>
      ))}
      {usage.quota_reset_date && (
        <p className="hint">Resets {timestamp(usage.quota_reset_date)}</p>
      )}
    </div>
  );
}
