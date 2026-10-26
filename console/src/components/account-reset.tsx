import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { data, type Schema } from "../api/client";
import { useApi } from "../api/session";
import { timestamp } from "../format";
import { Badge, Copy, Empty, ErrorNotice, Field, Loading, Modal } from "./ui";

type Attempt = {
  request: Schema["ResetRequest"];
  result?: Schema["ResetResult"];
};
export function availableCredits(
  usage: Schema["CodexUsage"],
  now = Date.now(),
) {
  return (usage.reset_credits?.credits ?? []).filter(
    (credit) =>
      credit.status === "available" &&
      credit.reset_type === "codex_rate_limits" &&
      (!credit.expires_at || Date.parse(credit.expires_at) > now),
  );
}
export function AccountReset({
  account,
  close,
}: {
  account: Schema["SubscriptionAccount"];
  close: () => void;
}) {
  const api = useApi();
  const cache = useQueryClient();
  const key = ["reset-attempt", account.id];
  const [attempt, setAttempt] = useState(() =>
    cache.getQueryData<Attempt>(key),
  );
  const [creditId, setCreditId] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const usage = useQuery({
    queryKey: ["codex-reset-usage", account.id],
    staleTime: 0,
    queryFn: ({ signal }) =>
      data(
        api.GET("/api/subscriptions/accounts/{account_id}/codex", {
          signal,
          params: { path: { account_id: account.id } },
        }),
      ),
  });
  const redeem = useMutation({
    mutationFn: (request: Schema["ResetRequest"]) =>
      data(
        api.POST("/api/subscriptions/accounts/{account_id}/reset", {
          params: { path: { account_id: account.id } },
          body: request,
        }),
      ),
    onSuccess: (result, request) => {
      const completed = { request, result };
      cache.setQueryData(key, completed);
      setAttempt(completed);
      void cache.invalidateQueries({ queryKey: ["subscriptions"] });
      void usage.refetch();
    },
  });
  const credits = usage.data ? availableCredits(usage.data) : [];
  return (
    <Modal
      title={`Reset allowance · ${account.name}`}
      description="Redeem one provider voucher to reset eligible Codex rate-limit windows. This consumes the selected voucher."
      close={() => {
        if (!redeem.isPending) close();
      }}
    >
      <div className="form">
        {attempt ? (
          <>
            <Badge>
              {attempt.result?.code ??
                (redeem.isPending ? "pending" : "unknown")}
            </Badge>
            <p>
              {attempt.result
                ? `Provider confirmed: ${attempt.result.code.replaceAll("_", " ")}. ${attempt.result.windows_reset ?? 0} windows reset.`
                : "Keep this request identity until the outcome is known. Retry sends the same account, voucher, and redemption UUID."}
            </p>
            <dl className="facts">
              <dt>Local account</dt>
              <dd>{account.id}</dd>
              <dt>Provider account</dt>
              <dd>{attempt.request.account_id}</dd>
              <dt>Voucher</dt>
              <dd>{attempt.request.credit_id}</dd>
            </dl>
            <Copy value={attempt.request.redeem_request_id} />
            <ErrorNotice error={redeem.error} />
            {!attempt.result && (
              <>
                <p className="hint">
                  This identity is retained while this Console session remains
                  connected. Copy it before reloading or disconnecting.
                </p>
                <button
                  className="primary"
                  disabled={redeem.isPending}
                  onClick={() => redeem.mutate(attempt.request)}
                >
                  {redeem.isPending ? "Redeeming…" : "Retry the same request"}
                </button>
              </>
            )}
            {attempt.result && (
              <button
                onClick={() => {
                  cache.removeQueries({ queryKey: key });
                  setAttempt(undefined);
                  setCreditId("");
                  setConfirmed(false);
                  redeem.reset();
                }}
              >
                Choose another voucher
              </button>
            )}
          </>
        ) : usage.isPending ? (
          <Loading />
        ) : (
          <>
            <ErrorNotice
              error={usage.error}
              retry={() => void usage.refetch()}
            />
            {usage.data?.reset_unavailable && (
              <p className="notice">{usage.data.reset_unavailable}</p>
            )}
            {credits.length ? (
              <>
                <Field label="Reset voucher">
                  <select
                    value={creditId}
                    onChange={(e) => setCreditId(e.target.value)}
                    required
                  >
                    <option value="">Choose a voucher</option>
                    {credits.map((credit) => (
                      <option key={credit.id} value={credit.id}>
                        {credit.title ?? credit.id} · expires{" "}
                        {timestamp(credit.expires_at)}
                      </option>
                    ))}
                  </select>
                </Field>
                <p className="hint">
                  {credits.find((c) => c.id === creditId)?.description}
                </p>
                <dl className="facts">
                  <dt>Local account</dt>
                  <dd>{account.id}</dd>
                  <dt>Provider account</dt>
                  <dd>{usage.data?.account_id}</dd>
                </dl>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(e) => setConfirmed(e.target.checked)}
                  />
                  Redeem this voucher for this account
                </label>
                <button
                  className="primary"
                  disabled={!confirmed || !creditId}
                  onClick={() => {
                    const request = {
                      account_id: usage.data!.account_id,
                      credit_id: creditId,
                      redeem_request_id: crypto.randomUUID(),
                    };
                    cache.setQueryDefaults(["reset-attempt"], {
                      gcTime: Infinity,
                    });
                    cache.setQueryData(key, { request });
                    setAttempt({ request });
                    redeem.mutate(request);
                  }}
                >
                  Redeem voucher
                </button>
              </>
            ) : (
              !usage.error && (
                <Empty title="No eligible reset vouchers">
                  Refresh to check the provider again.
                </Empty>
              )
            )}
          </>
        )}
        {attempt?.result && usage.error && (
          <p className="notice">
            Redemption remains confirmed. The latest allowance could not be
            refreshed.
          </p>
        )}
      </div>
    </Modal>
  );
}
