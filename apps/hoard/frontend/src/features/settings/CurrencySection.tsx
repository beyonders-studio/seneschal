import { useTranslation } from "react-i18next";

import { SettingsSection } from "@/features/settings/SettingsSection";

/** The currency every cost and price is in. */
export function CurrencySection() {
  const { t } = useTranslation("catalog");
  return <SettingsSection id="currency" title={t("settings.currency")} />;
}
