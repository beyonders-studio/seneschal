import { useTranslation } from "react-i18next";

import { SettingsSection } from "@/features/settings/SettingsSection";

/** The starting setups added so far, and adding another. */
export function SetupSection() {
  const { t } = useTranslation("setups");
  return <SettingsSection id="setups" title={t("section.title")} />;
}
