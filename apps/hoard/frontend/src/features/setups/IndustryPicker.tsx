import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { SetupRead } from "@/api/generated/hoard.schemas";
import { addSetup, startBlank, useListSetups } from "@/api/generated/setups/setups";
import { toast } from "@/components/Toaster";
import { useChangesSettings } from "@/features/settings/NamedList";
import { errorMessage } from "@/lib/errors";
import { Button } from "@/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";

/** The choice for a business Hoard has no starting setup for. */
const SOMETHING_ELSE = "something-else";

/** Asks an admin, the first time Hoard is opened, which kind of business this
 *  is, and starts Hoard from that industry's setup in the admin's words: each
 *  of its keys as the `setups.<setup>.<key>` words name it. "Something else"
 *  starts blank. Once answered, or once the organization has a unit or a
 *  setup, it is not asked again. Everything a setup adds shows elsewhere, so
 *  every read is refreshed after. */
export function IndustryPicker() {
  const { t } = useTranslation("setups");
  const queryClient = useQueryClient();
  const changes = useChangesSettings();
  const offered = useListSetups({ query: { enabled: changes } }).data;
  const [chosen, setChosen] = useState("");
  const choosing = useMutation({
    mutationFn: async (setup: SetupRead | undefined) => {
      if (!setup) {
        await startBlank();
        return;
      }
      await addSetup(setup.key, {
        words: Object.fromEntries(
          setup.words.map((word) => [word, t(`setups.${setup.key}.${word}`)])
        ),
      });
    },
    onSuccess: async (_answer, setup) => {
      await queryClient.invalidateQueries();
      if (setup) toast.success(t("picker.addedToast", { name: t(`setups.${setup.key}.name`) }));
    },
    onError: (error) => toast.error(errorMessage(error, t, "picker.error")),
  });
  if (!changes || !offered || offered.started) return null;
  if (offered.setups.some((setup) => setup.added)) return null;
  return (
    <section
      aria-labelledby="industry-picker"
      className="flex flex-col gap-3 rounded-lg border bg-card p-4"
    >
      <h2 id="industry-picker" className="font-semibold">
        {t("picker.title")}
      </h2>
      <p className="text-sm text-muted-foreground">{t("picker.body")}</p>
      <div className="flex flex-wrap items-center gap-3">
        <Select value={chosen} onValueChange={setChosen}>
          <SelectTrigger aria-label={t("picker.label")} className="w-72 max-w-full">
            <SelectValue placeholder={t("picker.placeholder")} />
          </SelectTrigger>
          <SelectContent>
            {offered.setups.map((setup) => (
              <SelectItem key={setup.key} value={setup.key}>
                {t(`setups.${setup.key}.name`)}
              </SelectItem>
            ))}
            <SelectItem value={SOMETHING_ELSE}>{t("picker.other")}</SelectItem>
          </SelectContent>
        </Select>
        <Button
          disabled={!chosen}
          pending={choosing.isPending}
          onClick={() => choosing.mutate(offered.setups.find((setup) => setup.key === chosen))}
        >
          {t("picker.start")}
        </Button>
      </div>
    </section>
  );
}
