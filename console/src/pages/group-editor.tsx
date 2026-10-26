import { IconButton } from "../components/icons";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { data, type Schema } from "../api/client";
import { useApi } from "../api/session";
import { Empty, ErrorNotice, Field, Modal } from "../components/ui";
import { SourceConflict } from "./model-editor";

type GroupView = Schema["ModelInventory"]["groups"][number];
export function GroupEditor({
  view,
  close,
}: {
  view?: GroupView;
  close: () => void;
}) {
  const api = useApi();
  const cache = useQueryClient();
  const [group, setGroup] = useState<Schema["SubscriptionGroupResource"]>(
    view?.resource ?? {
      schema_version: "1",
      kind: "subscription_group",
      id: "group-",
      name: "",
      provider: "codex",
      strategy: "ordered",
      allow_credits: false,
      accounts: [],
    },
  );
  const accounts = useQuery({
    queryKey: ["subscriptions"],
    queryFn: ({ signal }) => data(api.GET("/api/subscriptions", { signal })),
  });
  const members = group.accounts ?? [];
  const save = useMutation({
    mutationFn: () =>
      data(
        api.PUT("/api/subscription-groups", {
          body: {
            resource: group,
            relative_path:
              view?.relative_path ?? `subscription_groups/${group.id}.yaml`,
            expected_digest: view?.source_digest ?? null,
          },
        }),
      ),
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: ["models"] });
      await cache.invalidateQueries({ queryKey: ["subscriptions"] });
      close();
    },
  });
  function move(index: number, delta: number) {
    const next = [...members];
    [next[index], next[index + delta]] = [next[index + delta], next[index]];
    setGroup({ ...group, accounts: next });
  }
  return (
    <Modal
      title={view ? "Edit subscription group" : "Add subscription group"}
      close={() => {
        if (!save.isPending) close();
      }}
    >
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <fieldset className="form" disabled={save.isPending}>
          <div className="form-grid">
            <Field label="Name">
              <input
                required
                value={group.name}
                onChange={(e) => setGroup({ ...group, name: e.target.value })}
              />
            </Field>
            <Field label="Resource ID">
              <input
                required
                value={group.id}
                disabled={!!view}
                onChange={(e) => setGroup({ ...group, id: e.target.value })}
              />
            </Field>
          </div>
          <div className="form-grid">
            <Field label="Provider">
              <select
                value={group.provider}
                disabled={!!view}
                onChange={(e) =>
                  setGroup({
                    ...group,
                    provider: e.target.value as "codex" | "grok",
                    accounts: [],
                  })
                }
              >
                <option value="codex">Codex</option>
                <option value="grok">Grok</option>
              </select>
            </Field>
            <Field label="Selection strategy">
              <select
                value={group.strategy}
                onChange={(e) =>
                  setGroup({
                    ...group,
                    strategy: e.target.value as "ordered" | "weighted",
                  })
                }
              >
                <option value="ordered">Ordered</option>
                <option value="weighted">Weighted</option>
              </select>
            </Field>
          </div>
          <label className="check">
            <input
              type="checkbox"
              checked={group.allow_credits}
              onChange={(e) =>
                setGroup({ ...group, allow_credits: e.target.checked })
              }
            />
            Allow paid credits when included allowance is exhausted
          </label>
          <div className="stack">
            <h3>Accounts</h3>
            <ErrorNotice error={accounts.error} />
            {members.length === 0 && <Empty title="No accounts selected" />}
            {members.map((member, index) => (
              <div className="member-row" key={member.account}>
                <div className="grow">
                  <strong>
                    {accounts.data?.accounts.find(
                      (a) => a.account.id === member.account,
                    )?.account.name ?? member.account}
                  </strong>
                  <code className="subline">{member.account}</code>
                </div>
                {group.strategy === "weighted" && (
                  <Field label="Weight">
                    <input
                      aria-label={`Weight for ${member.account}`}
                      type="number"
                      min={1}
                      max={1000}
                      required
                      value={member.weight ?? 1}
                      onChange={(e) =>
                        setGroup({
                          ...group,
                          accounts: members.map((m, i) =>
                            i === index
                              ? { ...m, weight: Number(e.target.value) }
                              : m,
                          ),
                        })
                      }
                    />
                  </Field>
                )}
                <div className="actions">
                  <IconButton
                    icon="up"
                    type="button"
                    label={`Move ${member.account} up`}
                    disabled={index === 0}
                    onClick={() => move(index, -1)}
                  />
                  <IconButton
                    icon="down"
                    type="button"
                    label={`Move ${member.account} down`}
                    disabled={index === members.length - 1}
                    onClick={() => move(index, 1)}
                  />
                  <IconButton
                    icon="close"
                    type="button"
                    label={`Remove ${member.account}`}
                    onClick={() =>
                      setGroup({
                        ...group,
                        accounts: members.filter((_, i) => i !== index),
                      })
                    }
                  />
                </div>
              </div>
            ))}
            <Field label="Add account">
              <select
                value=""
                onChange={(e) => {
                  if (e.target.value)
                    setGroup({
                      ...group,
                      accounts: [
                        ...members,
                        { account: e.target.value, weight: 1 },
                      ],
                    });
                }}
              >
                <option value="">Choose an account</option>
                {accounts.data?.accounts
                  .filter(
                    (a) =>
                      a.account.provider === group.provider &&
                      !members.some((m) => m.account === a.account.id),
                  )
                  .map((a) => (
                    <option key={a.account.id} value={a.account.id}>
                      {a.account.name}
                      {!a.account.enabled ? " (disabled)" : ""}
                    </option>
                  ))}
              </select>
            </Field>
          </div>
        </fieldset>
        <ErrorNotice error={save.error} />
        <SourceConflict error={save.error} />
        <div className="form-actions">
          <button type="button" disabled={save.isPending} onClick={close}>
            Cancel
          </button>
          <button className="primary" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save group"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
