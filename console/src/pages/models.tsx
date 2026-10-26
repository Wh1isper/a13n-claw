import { Icon, IconButton } from "../components/icons";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { data, type Schema } from "../api/client";
import { useApi } from "../api/session";
import {
  Badge,
  Empty,
  ErrorNotice,
  Loading,
  PageHeader,
  Panel,
  ConfirmAction,
} from "../components/ui";
import { Accounts, Credentials } from "./accounts";
import { ModelEditor } from "./model-editor";
import { GroupEditor } from "./group-editor";

type Inventory = Schema["ModelInventory"];
export function Models() {
  const api = useApi();
  const cache = useQueryClient();
  const [params] = useSearchParams();
  const tab = ["accounts", "groups", "credentials"].includes(
    params.get("tab") ?? "",
  )
    ? params.get("tab")!
    : "models";
  const [model, setModel] = useState<
    Inventory["models"][number] | "new" | null
  >(null);
  const [group, setGroup] = useState<
    Inventory["groups"][number] | "new" | null
  >(null);
  const [remove, setRemove] = useState<
    Inventory["models"][number] | Inventory["groups"][number] | null
  >(null);
  const inventory = useQuery({
    queryKey: ["models"],
    queryFn: ({ signal }) => data(api.GET("/api/models", { signal })),
  });
  const choices = useQuery({
    queryKey: ["model-choices"],
    queryFn: ({ signal }) => data(api.GET("/api/models/choices", { signal })),
  });
  return (
    <>
      <PageHeader
        title="Models & Accounts"
        actions={
          <IconButton
            icon="refresh"
            label="Refresh"
            disabled={inventory.isFetching}
            onClick={() => {
              void inventory.refetch();
              void cache.invalidateQueries({ queryKey: ["subscriptions"] });
              void cache.invalidateQueries({ queryKey: ["account"] });
            }}
          />
        }
      />
      <nav className="tabs" aria-label="Model settings">
        {["models", "accounts", "groups", "credentials"].map((key) => (
          <Link
            key={key}
            to={key === "models" ? "/models" : `/models?tab=${key}`}
            aria-current={tab === key ? "page" : undefined}
          >
            {key[0].toUpperCase() + key.slice(1)}
          </Link>
        ))}
      </nav>
      <ErrorNotice
        error={inventory.error}
        retry={() => void inventory.refetch()}
      />
      <ErrorNotice error={choices.error} retry={() => void choices.refetch()} />
      {inventory.isPending || choices.isPending ? (
        <Loading />
      ) : tab === "accounts" && choices.data ? (
        <Accounts choices={choices.data} />
      ) : tab === "credentials" ? (
        <Credentials />
      ) : tab === "groups" ? (
        <Panel
          title="Subscription groups"
          actions={
            <button className="primary" onClick={() => setGroup("new")}>
              <Icon name="plus" /> Add group
            </button>
          }
        >
          {inventory.data?.groups.length ? (
            <div className="table-scroll">
              <table className="responsive-table">
                <thead>
                  <tr>
                    <th>Group</th>
                    <th>Provider</th>
                    <th>Strategy</th>
                    <th>Accounts</th>
                    <th>Paid credits</th>
                    <th>
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {inventory.data.groups.map((view) => (
                    <tr key={view.resource.id}>
                      <td data-label="Group">
                        <strong>{view.resource.name}</strong>
                        <code className="subline">{view.resource.id}</code>
                      </td>
                      <td data-label="Provider">{view.resource.provider}</td>
                      <td data-label="Strategy">{view.resource.strategy}</td>
                      <td data-label="Accounts">
                        {view.resource.accounts?.length ?? 0}
                      </td>
                      <td data-label="Paid credits">
                        {view.resource.allow_credits ? "On" : "Off"}
                      </td>
                      <td data-label="">
                        <div className="actions">
                          <IconButton
                            icon="edit"
                            label="Edit"
                            onClick={() => setGroup(view)}
                          />
                          <IconButton
                            icon="trash"
                            label="Delete"
                            className="danger"
                            onClick={() => setRemove(view)}
                          />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="No subscription groups" />
          )}
        </Panel>
      ) : (
        <Panel
          title="Models"
          actions={
            <button
              className="primary"
              disabled={!choices.data?.connections?.length}
              onClick={() => setModel("new")}
            >
              <Icon name="plus" /> Add model
            </button>
          }
        >
          {inventory.data?.models.length ? (
            <div className="table-scroll">
              <table className="responsive-table">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Route</th>
                    <th>Authentication</th>
                    <th>
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {inventory.data.models.map((view) => (
                    <tr key={view.resource.id}>
                      <td data-label="Model">
                        <strong>{view.resource.name}</strong>
                        <code className="subline">{view.resource.id}</code>
                      </td>
                      <td data-label="Route">
                        <code>{view.resource.route}</code>
                      </td>
                      <td data-label="Authentication">
                        <Badge>{view.resource.authentication.kind}</Badge>
                        <span className="subline">
                          {view.resource.authentication.kind === "api_key"
                            ? (view.resource.authentication.credential_ref ??
                              view.resource.authentication.env)
                            : "group" in view.resource.authentication
                              ? (view.resource.authentication.group ??
                                "Provider default")
                              : "Selected account"}
                        </span>
                      </td>
                      <td data-label="">
                        <div className="actions">
                          <IconButton
                            icon="edit"
                            label="Edit"
                            onClick={() => setModel(view)}
                          />
                          <IconButton
                            icon="trash"
                            label="Delete"
                            className="danger"
                            onClick={() => setRemove(view)}
                          />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="No models yet" />
          )}
        </Panel>
      )}
      {model && choices.data && (
        <ModelEditor
          view={model === "new" ? undefined : model}
          choices={choices.data}
          groups={inventory.data?.groups ?? []}
          close={() => setModel(null)}
        />
      )}
      {group && (
        <GroupEditor
          view={group === "new" ? undefined : group}
          close={() => setGroup(null)}
        />
      )}
      {remove && (
        <ConfirmAction
          title={`Delete ${remove.resource.name}?`}
          description={`Delete ${remove.relative_path}.`}
          action={async () => {
            await data(
              api.DELETE(
                remove.resource.kind === "model"
                  ? "/api/models"
                  : "/api/subscription-groups",
                {
                  body: {
                    relative_path: remove.relative_path,
                    expected_digest: remove.source_digest,
                  },
                },
              ),
            );
            await cache.invalidateQueries({ queryKey: ["models"] });
          }}
          close={() => setRemove(null)}
        />
      )}
    </>
  );
}
