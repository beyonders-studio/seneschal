import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { SettingsSection } from "@/features/settings/SettingsSection";
import { Button } from "@/ui/button";

/** Where an admin brings records over from another program's exports. */
export function ImportSection() {
  const { t } = useTranslation("import");
  return (
    <SettingsSection id="import" title={t("section.title")}>
      <p className="text-sm text-muted-foreground">{t("section.description")}</p>
      <div>
        <Button asChild variant="outline">
          <Link to="/settings/import">{t("section.stocksmith")}</Link>
        </Button>
      </div>
    </SettingsSection>
  );
}
