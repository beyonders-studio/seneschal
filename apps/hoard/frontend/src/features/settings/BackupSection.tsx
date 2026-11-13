import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import {
  getReadBackupPeriodQueryKey,
  useReadBackupPeriod,
} from "@/api/generated/documents/documents";
import { Regime } from "@/api/generated/hoard.schemas";
import {
  getReadSettingsQueryKey,
  updateSettings,
  useReadSettings,
} from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { useChangesSettings } from "@/features/settings/NamedList";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { errorMessage } from "@/lib/errors";
import { Checkbox } from "@/ui/checkbox";
import { Label } from "@/ui/label";

const REGIMES = Object.values(Regime);

/**
 * The rules the organization keeps records for, which an admin ticks, what
 * each asks, and the longest of them. The words say what the rules ask;
 * keeping backups that long is the infrastructure's, not Hoard's.
 */
export function BackupSection() {
  const { t } = useTranslation("documents");
  const settings = useReadSettings().data;
  const months = useReadBackupPeriod().data?.months;
  const changes = useChangesSettings();
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: (regimes: Regime[]) => updateSettings({ regimes }),
    onSuccess: async (saved) => {
      queryClient.setQueryData(getReadSettingsQueryKey(), saved);
      await queryClient.invalidateQueries({ queryKey: getReadBackupPeriodQueryKey() });
    },
    onError: (error) => toast.error(errorMessage(error, t, "backups.saveFailed")),
  });
  const ticked = new Set(settings?.regimes);
  const shown = changes ? REGIMES : REGIMES.filter((regime) => ticked.has(regime));
  const tick = (regime: Regime, on: boolean) =>
    save.mutate(REGIMES.filter((each) => (each === regime ? on : ticked.has(each))));
  const duration = (count: number) =>
    count % 12 === 0 ? t("backups.years", { count: count / 12 }) : t("backups.months", { count });
  return (
    <SettingsSection id="backups" title={t("backups.title")}>
      <p className="text-sm text-muted-foreground">{t("backups.description")}</p>
      {settings && (
        <>
          <ul className="flex flex-col gap-3">
            {shown.map((regime) => (
              <li key={regime} className="flex items-start gap-2">
                {changes && (
                  <Checkbox
                    id={`regime-${regime}`}
                    className="mt-0.5"
                    checked={ticked.has(regime)}
                    disabled={save.isPending}
                    onCheckedChange={(checked) => tick(regime, checked === true)}
                  />
                )}
                <div className="flex flex-col gap-0.5">
                  <Label htmlFor={changes ? `regime-${regime}` : undefined}>
                    {t(`backups.regimes.${regime}.name`)}
                  </Label>
                  <span className="text-xs text-muted-foreground">
                    {t(`backups.regimes.${regime}.asks`)}
                  </span>
                </div>
              </li>
            ))}
          </ul>
          <p className="text-sm">
            {months == null ? t("backups.none") : t("backups.period", { period: duration(months) })}
          </p>
        </>
      )}
    </SettingsSection>
  );
}
