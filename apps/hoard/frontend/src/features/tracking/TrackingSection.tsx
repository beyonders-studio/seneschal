import { useTranslation } from "react-i18next";

import { SettingsSection } from "@/features/settings/SettingsSection";

/** How runs and purchases are tracked on Initiative's tasks. */
export function TrackingSection() {
  const { t } = useTranslation("tracking");
  return <SettingsSection id="tracking" title={t("section.title")} />;
}
