import { useTranslation } from "react-i18next";

import { SettingsSection } from "@/features/settings/SettingsSection";

/** The rules the organization keeps records for, which an admin ticks, and
 *  how long its backups are kept for them. */
export function BackupSection() {
  const { t } = useTranslation("documents");
  return (
    <SettingsSection id="backups" title={t("backups.title")}>
      <p className="text-sm text-muted-foreground">{t("backups.none")}</p>
    </SettingsSection>
  );
}
