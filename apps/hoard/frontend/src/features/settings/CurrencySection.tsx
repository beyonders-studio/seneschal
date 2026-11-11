import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  getReadSettingsQueryKey,
  updateSettings,
  useReadSettings,
} from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { useChangesSettings } from "@/features/settings/NamedList";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { errorMessage } from "@/lib/errors";
import { Button } from "@/ui/button";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";
import { displayNames } from "@/ui/lib/intl";

/** An ISO 4217 code's shape. */
const CODE = /^[A-Z]{3}$/;

/** The currency every cost and price is in, which an admin changes. */
export function CurrencySection() {
  const { t } = useTranslation("catalog");
  const currency = useReadSettings().data?.currency;
  const changes = useChangesSettings();
  const name = useCurrencyName();
  return (
    <SettingsSection id="currency" title={t("settings.currency.title")}>
      <p className="text-sm text-muted-foreground">{t("settings.currency.description")}</p>
      {currency &&
        (changes ? (
          <CurrencyForm key={currency} current={currency} />
        ) : (
          <p className="text-sm">{name(currency)}</p>
        ))}
    </SettingsSection>
  );
}

/** A currency's name in the reader's language, with its code. */
function useCurrencyName(): (code: string) => string {
  const { i18n } = useTranslation();
  const names = displayNames(i18n.resolvedLanguage ?? "en", { type: "currency" });
  return (code) => `${names.of(code)} (${code})`;
}

function CurrencyForm({ current }: { current: string }) {
  const { t } = useTranslation("catalog");
  const queryClient = useQueryClient();
  const name = useCurrencyName();
  const [code, setCode] = useState(current);
  const save = useMutation({
    mutationFn: () => updateSettings({ currency: code }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: getReadSettingsQueryKey() });
      toast.success(t("settings.currency.saved"));
    },
    onError: (error) => toast.error(errorMessage(error, t, "settings.saveFailed")),
  });
  const valid = CODE.test(code);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
      <Field
        label={t("settings.currency.code")}
        hint={valid ? name(code) : t("settings.currency.codeHint")}
      >
        <Input
          value={code}
          maxLength={3}
          className="w-24 uppercase"
          onChange={(event) => setCode(event.target.value.toUpperCase())}
        />
      </Field>
      <Button type="submit" pending={save.isPending} disabled={!valid || code === current}>
        {t("hoard:common.save")}
      </Button>
    </form>
  );
}
