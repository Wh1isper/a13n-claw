import { Button, FormField, React, SearchPicker } from "@a13n/console";
import styles from "../app.module.css";
import { useCopy } from "../copy";
import {
  useProjectResources,
  type Connector,
  type ResourceKind,
} from "../data";
import { ResourceLink } from "../primitives";

export const field = (form: FormData, name: string) =>
  String(form.get(name) ?? "").trim();
export const items = (form: FormData, name: string) =>
  field(form, name)
    .split(/[\s,]+/)
    .filter(Boolean);
export const list = (config: Connector["config"] | undefined, key: string) =>
  Array.isArray(config?.[key]) ? config[key].join("\n") : "";

const resourceCreation: Record<ResourceKind, string> = {
  agent: "agents/new",
  account: "settings/service-accounts",
  environment: "environments/instances",
  template: "environments/new",
  browser: "browsers",
  browser_provider: "browsers",
  model: "models",
};

export function ResourcePicker({
  label,
  kind,
  account,
  value,
  onChange,
  optional,
  disabled,
}: {
  label: string;
  kind: ResourceKind;
  account?: string;
  value: string;
  onChange: (value: string) => void;
  optional?: boolean;
  disabled?: boolean;
}) {
  const { copy } = useCopy();
  const choices = useProjectResources(kind, !disabled, account);
  const rows = choices.data?.pages.flatMap((page) => page.items) ?? [];
  const selected =
    value && !rows.some((row) => row.id === value)
      ? [{ value, label: value }]
      : [];
  const options = [
    ...(optional ? [{ value: "__none__", label: copy("None", "不绑定") }] : []),
    ...selected,
    ...rows.map(({ id, name }) => ({ value: id, label: name })),
  ];
  const id = React.useId();
  return (
    <>
      <FormField label={label} data-field-layout="setting">
        <SearchPicker
          id={id}
          label={label}
          value={value || (optional ? "__none__" : undefined)}
          disabled={disabled}
          placeholder={
            disabled
              ? copy("Select a service account first", "请先选择服务账号")
              : choices.isPending
                ? copy("Loading…", "加载中…")
                : copy("Select a resource", "选择资源")
          }
          emptyMessage={copy("No matching resources", "没有匹配的资源")}
          groups={[{ label, options }]}
          onValueChange={(value) => onChange(value === "__none__" ? "" : value)}
          footer={
            choices.hasNextPage && (
              <Button
                type="button"
                variant="ghost"
                disabled={choices.isFetchingNextPage}
                onClick={() => void choices.fetchNextPage()}
              >
                {copy("Load more", "加载更多")}
              </Button>
            )
          }
        />
      </FormField>
      {!disabled &&
        (choices.isError || (!choices.isPending && !rows.length)) && (
          <div className={styles.actions}>
            {!choices.isError && (
              <ResourceLink path={resourceCreation[kind]}>
                {copy("Create in Console", "前往控制台创建")}
              </ResourceLink>
            )}
            <Button
              type="button"
              variant="ghost"
              onClick={() => void choices.refetch()}
            >
              {copy("Refresh choices", "刷新选项")}
            </Button>
          </div>
        )}
    </>
  );
}
