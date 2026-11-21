import { Button, React, useToast } from "@a13n/console";
import { useCopy } from "../copy";
import { Icon } from "./icons";

export function CopyButton({ value }: { value: string }) {
  const { copy } = useCopy();
  const toast = useToast();
  const [copied, setCopied] = React.useState(false);
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      onClick={() => {
        void navigator.clipboard.writeText(value).then(
          () => {
            setCopied(true);
            toast.add({ title: copy("Copied", "已复制") });
            window.setTimeout(() => setCopied(false), 2000);
          },
          () => toast.add({ title: copy("Could not copy", "无法复制") }),
        );
      }}
    >
      <Icon name={copied ? "check" : "copy"} />
      {copied ? copy("Copied", "已复制") : copy("Copy", "复制")}
    </Button>
  );
}
