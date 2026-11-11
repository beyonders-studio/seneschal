import { useTranslation } from "react-i18next";

import { SettingsSection } from "@/features/settings/SettingsSection";

/** The places stock is kept. */
export function LocationsSection() {
  const { t } = useTranslation("catalog");
  return <SettingsSection id="locations" title={t("settings.locations")} />;
}
