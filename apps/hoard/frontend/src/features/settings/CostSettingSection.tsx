import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  getReadSettingsQueryKey,
  updateSettings,
  useReadSettings,
} from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { orNull, useShare } from "@/features/items/format";
import { useChangesSettings } from "@/features/settings/NamedList";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { errorMessage } from "@/lib/errors";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { Button } from "@/ui/button";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";
import { Label } from "@/ui/label";
import { Switch } from "@/ui/switch";

/** The settings that are costs, each with its words under `settings.<words>`
 *  in the catalog namespace. */
const COST_SETTINGS = {
  labour_rate: "labourRate",
  overhead: "overhead",
} as const;

type CostSetting = keyof typeof COST_SETTINGS;

/** A setting that is a cost, which an admin sets, shown only to a member who
 *  sees costs: what an hour of work costs, or the percentage added to what
 *  goods cost for overhead. */
export function CostSettingSection({ setting }: { setting: CostSetting }) {
  const { t } = useTranslation("catalog");
  const settings = useReadSettings().data;
  const changes = useChangesSettings();
  const money = useMoney();
  const share = useShare();
  const words = COST_SETTINGS[setting];
  if (!useSeesCosts() || !settings) return null;
  const value = settings[setting];
  return (
    <SettingsSection id={words} title={t(`settings.${words}.title`)}>
      <p className="text-sm text-muted-foreground">{t(`settings.${words}.description`)}</p>
      {changes ? (
        <CostSettingForm key={value} setting={setting} current={value} />
      ) : (
        <p className="text-sm">
          {value === null
            ? t(`settings.${words}.unset`)
            : setting === "overhead"
              ? share(value)
              : money(value, { exact: true })}
        </p>
      )}
    </SettingsSection>
  );
}

function CostSettingForm({ setting, current }: { setting: CostSetting; current: string | null }) {
  const { t } = useTranslation("catalog");
  const queryClient = useQueryClient();
  const words = COST_SETTINGS[setting];
  const [value, setValue] = useState(current ?? "");
  const save = useMutation({
    mutationFn: () => updateSettings({ [setting]: orNull(value) }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: getReadSettingsQueryKey() });
      toast.success(t(`settings.${words}.saved`));
    },
    onError: (error) => toast.error(errorMessage(error, t, "settings.saveFailed")),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
      <Field label={t(`settings.${words}.field`)} hint={t(`settings.${words}.hint`)}>
        <Input
          inputMode="decimal"
          value={value}
          className="w-32"
          onChange={(event) => setValue(event.target.value)}
        />
      </Field>
      <Button type="submit" pending={save.isPending} disabled={orNull(value) === current}>
        {t("hoard:common.save")}
      </Button>
    </form>
  );
}

/** Whether the tax paid on a purchase counts in what its lines cost, for a
 *  business that can't claim it back: shown, like the costs beside it, only
 *  to a member who sees costs, and switched by an admin. */
export function PurchaseTaxSection() {
  const { t } = useTranslation("catalog");
  const queryClient = useQueryClient();
  const settings = useReadSettings().data;
  const changes = useChangesSettings();
  const save = useMutation({
    mutationFn: (counted: boolean) => updateSettings({ purchase_tax_in_cost: counted }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: getReadSettingsQueryKey() });
      toast.success(t("settings.purchaseTax.saved"));
    },
    onError: (error) => toast.error(errorMessage(error, t, "settings.saveFailed")),
  });
  if (!useSeesCosts() || !settings) return null;
  return (
    <SettingsSection id="purchaseTax" title={t("settings.purchaseTax.title")}>
      <p className="text-sm text-muted-foreground">{t("settings.purchaseTax.description")}</p>
      <span className="inline-flex items-center gap-2">
        <Switch
          id="purchase-tax-in-cost"
          checked={settings.purchase_tax_in_cost}
          disabled={!changes || save.isPending}
          onCheckedChange={(counted) => save.mutate(counted)}
        />
        <Label htmlFor="purchase-tax-in-cost" className="text-sm font-normal">
          {t("settings.purchaseTax.field")}
        </Label>
      </span>
    </SettingsSection>
  );
}
