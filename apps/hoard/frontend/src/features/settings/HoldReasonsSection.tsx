import { useTranslation } from "react-i18next";

import { SettingsSection } from "@/features/settings/SettingsSection";

/** The reasons a lot is put on hold: the default ones, which people rename or
 *  remove, and their own. */
export function HoldReasonsSection() {
  const { t } = useTranslation("holds");
  return (
    <SettingsSection id="hold-reasons" title={t("reasons.title")}>
      <p className="text-sm text-muted-foreground">{t("reasons.empty")}</p>
    </SettingsSection>
  );
}
