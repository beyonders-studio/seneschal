import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { SettingsSection } from "@/features/settings/SettingsSection";
import { Button } from "@/ui/button";

/** Where an admin brings records over from another program's exports, or
 *  back from Hoard's own spreadsheets. */
export function ImportSection() {
  const { t } = useTranslation("import");
  return (
    <SettingsSection id="import" title={t("section.title")}>
      <p className="text-sm text-muted-foreground">{t("section.description")}</p>
      <div className="flex flex-wrap gap-2">
        <Button asChild variant="outline">
          <Link to="/settings/import/$importer" params={{ importer: "spreadsheets" }}>
            {t("importers.spreadsheets.title")}
          </Link>
        </Button>
        <Button asChild variant="outline">
          <Link to="/settings/import/$importer" params={{ importer: "stocksmith" }}>
            {t("importers.stocksmith.title")}
          </Link>
        </Button>
      </div>
    </SettingsSection>
  );
}
