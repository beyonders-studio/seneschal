import { useTranslation } from "react-i18next";

import type { AdjustmentOptionItem } from "@/api/generated/hoard.schemas";
import { useMovementLabels } from "@/features/adjustments/labels";
import { Combobox } from "@/ui/combobox";
import { Field } from "@/ui/field";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";

/** The location a movement happens at: the main location until there are
 *  others to choose. `value` is null for the main location before it is made. */
export function LocationField({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (value: number) => void;
}) {
  const { t } = useTranslation("movements");
  const { options, location } = useMovementLabels();
  const locations = options?.locations ?? [];
  const chosen = value ?? locations.find((place) => place.main)?.id;
  return (
    <Field label={t("hoard:common.location")}>
      <Select
        value={chosen === undefined ? "" : String(chosen)}
        onValueChange={(next) => onChange(Number(next))}
        disabled={locations.length < 2}
      >
        <SelectTrigger>
          <SelectValue placeholder={t("hoard:common.mainLocation")} />
        </SelectTrigger>
        <SelectContent>
          {locations.map((place) => (
            <SelectItem key={place.id} value={String(place.id)}>
              {location(place.id)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  );
}

/** An item to move: a material or a product's variation, or only the
 *  materials that carry stock with `materials`. */
export function ItemField({
  label,
  value,
  onChange,
  materials = false,
}: {
  label: string;
  value: number | null;
  onChange: (item: AdjustmentOptionItem) => void;
  materials?: boolean;
}) {
  const { t } = useTranslation("movements");
  const { options, item } = useMovementLabels();
  const items = (options?.items ?? []).filter((one) =>
    materials ? one.kind === "material" : one.inventoriable
  );
  return (
    <Field label={label}>
      <Combobox
        items={items.map((one) => ({
          value: String(one.id),
          label: item(one),
          hint: t(`item.kinds.${one.kind}`),
        }))}
        value={value === null ? null : String(value)}
        onValueChange={(next) => {
          const picked = items.find((one) => String(one.id) === next);
          if (picked) onChange(picked);
        }}
        placeholder={t("item.choose")}
        searchPlaceholder={t("hoard:common.search")}
        emptyMessage={t("item.none")}
      />
    </Field>
  );
}
