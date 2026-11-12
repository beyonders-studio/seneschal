import { useTranslation } from "react-i18next";

import { SettingsSection } from "@/features/settings/SettingsSection";

/** The community's connected stores: how each connection stands, and where
 *  each store's locations take their stock from. */
export function StoresSection() {
  const { t } = useTranslation("stores");
  return <SettingsSection id="stores" title={t("section.title")} />;
}
