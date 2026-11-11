import { useTranslation } from "react-i18next";

import { SettingsSection } from "@/features/settings/SettingsSection";

/** The reasons stock is adjusted by hand. */
export function ReasonsSection() {
  const { t } = useTranslation("catalog");
  return <SettingsSection id="reasons" title={t("settings.reasons")} />;
}
