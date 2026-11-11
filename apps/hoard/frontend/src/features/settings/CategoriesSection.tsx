import { useTranslation } from "react-i18next";

import { SettingsSection } from "@/features/settings/SettingsSection";

/** The categories materials and products are sorted into. */
export function CategoriesSection() {
  const { t } = useTranslation("catalog");
  return <SettingsSection id="categories" title={t("settings.categories")} />;
}
