import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { Picking, type SettingsCategoryRead } from "@/api/generated/hoard.schemas";
import {
  getListSettingsCategoriesQueryKey,
  updateSettingsCategory,
} from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { useChangesSettings } from "@/features/settings/NamedList";
import { errorMessage } from "@/lib/errors";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";

/** Which of a category's lots goes first, which an admin picks. */
export function PickingField({ category }: { category: SettingsCategoryRead }) {
  const { t } = useTranslation("holds");
  const queries = useQueryClient();
  const changes = useChangesSettings();
  const save = useMutation({
    mutationFn: (picking: Picking) => updateSettingsCategory(category.id, { picking }),
    onSuccess: () => queries.invalidateQueries({ queryKey: getListSettingsCategoriesQueryKey() }),
    onError: (error) => toast.error(errorMessage(error, t, "picking.failed")),
  });
  return (
    <Select
      value={category.picking}
      onValueChange={(next) => {
        const rule = Object.values(Picking).find((each) => each === next);
        if (rule) save.mutate(rule);
      }}
      disabled={!changes || save.isPending}
    >
      <SelectTrigger aria-label={t("picking.label", { name: category.name })} className="min-w-48">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {Object.values(Picking).map((rule) => (
          <SelectItem key={rule} value={rule}>
            {t(`picking.rules.${rule}`)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
