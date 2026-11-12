import { useTranslation } from "react-i18next";

import { useListSetups } from "@/api/generated/setups/setups";
import { useChangesSettings } from "@/features/settings/NamedList";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { useAddingSetup } from "@/features/setups/adding";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";

/** The starting setups Hoard offers, which are added, and adding another. */
export function SetupSection() {
  const { t } = useTranslation("setups");
  const setups = useListSetups().data?.setups ?? [];
  const changes = useChangesSettings();
  const adding = useAddingSetup();
  return (
    <SettingsSection id="setups" title={t("section.title")}>
      <p className="text-sm text-muted-foreground">{t("section.intro")}</p>
      <ul className="flex flex-col gap-3">
        {setups.map((setup) => (
          <li key={setup.key} className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-col gap-1">
              <span className="font-medium">{t(`setups.${setup.key}.name`)}</span>
              <span className="text-sm text-muted-foreground">
                {t(`setups.${setup.key}.description`)}
              </span>
            </div>
            {setup.added ? (
              <Badge variant="secondary">{t("section.added")}</Badge>
            ) : (
              changes && (
                <Button
                  variant="outline"
                  size="sm"
                  pending={adding.isPending && adding.variables?.key === setup.key}
                  onClick={() => adding.mutate(setup)}
                >
                  {t("section.add")}
                </Button>
              )
            )}
          </li>
        ))}
      </ul>
    </SettingsSection>
  );
}
