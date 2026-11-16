import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  getReadSettingsQueryKey,
  updateSettings,
  useReadSettings,
} from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { orNull } from "@/features/items/format";
import { useChangesSettings } from "@/features/settings/NamedList";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { errorMessage } from "@/lib/errors";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { Button } from "@/ui/button";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";

/** What an hour of work costs, which an admin sets; a cost, so shown only to
 *  a member who sees costs. */
export function LabourRateSection() {
  const { t } = useTranslation("catalog");
  const settings = useReadSettings().data;
  const changes = useChangesSettings();
  const money = useMoney();
  if (!useSeesCosts() || !settings) return null;
  const rate = settings.labour_rate;
  return (
    <SettingsSection id="labour-rate" title={t("settings.labourRate.title")}>
      <p className="text-sm text-muted-foreground">{t("settings.labourRate.description")}</p>
      {changes ? (
        <LabourRateForm key={rate} current={rate} />
      ) : (
        <p className="text-sm">
          {rate === null ? t("settings.labourRate.unset") : money(rate, { exact: true })}
        </p>
      )}
    </SettingsSection>
  );
}

function LabourRateForm({ current }: { current: string | null }) {
  const { t } = useTranslation("catalog");
  const queryClient = useQueryClient();
  const [rate, setRate] = useState(current ?? "");
  const save = useMutation({
    mutationFn: () => updateSettings({ labour_rate: orNull(rate) }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: getReadSettingsQueryKey() });
      toast.success(t("settings.labourRate.saved"));
    },
    onError: (error) => toast.error(errorMessage(error, t, "settings.saveFailed")),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
      <Field label={t("settings.labourRate.perHour")} hint={t("settings.labourRate.hint")}>
        <Input
          inputMode="decimal"
          value={rate}
          className="w-32"
          onChange={(event) => setRate(event.target.value)}
        />
      </Field>
      <Button type="submit" pending={save.isPending} disabled={orNull(rate) === current}>
        {t("hoard:common.save")}
      </Button>
    </form>
  );
}
