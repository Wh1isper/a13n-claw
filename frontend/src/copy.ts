import { useTranslation } from "@a13n/console";

export function useCopy() {
  const { i18n } = useTranslation();
  const language = i18n.language || "en";
  return {
    language,
    copy: (english: string, chinese: string) =>
      language.toLowerCase().startsWith("zh") ? chinese : english,
  };
}
