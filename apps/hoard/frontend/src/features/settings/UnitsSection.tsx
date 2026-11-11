import { useTranslation } from "react-i18next";

import { SettingsSection } from "@/features/settings/SettingsSection";

/** The units stock is counted and bought in. */
export function UnitsSection() {
  const { t } = useTranslation("catalog");
  return <SettingsSection id="units" title={t("settings.units")} />;
}
