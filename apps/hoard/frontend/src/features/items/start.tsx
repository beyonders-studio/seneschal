import { useTranslation } from "react-i18next";

import type { ItemBin, ItemStart } from "@/api/generated/hoard.schemas";
import { toast } from "@/components/Toaster";
import { LocationField } from "@/features/adjustments/fields";
import { BinFields, NO_BIN, trimmedBin } from "@/features/items/bins";
import { orNull } from "@/features/items/format";
import { IMAGE_TYPES, pictureRefused } from "@/features/items/ItemPicture";
import { useSeesCosts } from "@/lib/permissions";
import { Field } from "@/ui/field";
import { FileDropArea } from "@/ui/file-drop";
import { Input } from "@/ui/input";

/** What a new material or base starts with, as typed. */
export interface Start {
  picture: File | null;
  /** Null for the main location. */
  locationId: number | null;
  quantity: string;
  unitCost: string;
  bin: ItemBin;
}

export const NO_START: Start = {
  picture: null,
  locationId: null,
  quantity: "",
  unitCost: "",
  bin: NO_BIN,
};

/** Where a new item starts, as the API takes it: its stock only when it
 *  keeps stock, and the unit cost only from a member who sees costs. */
export const startBody = (start: Start, stocked: boolean, seesCosts: boolean): ItemStart => ({
  location_id: start.locationId,
  quantity: stocked ? orNull(start.quantity) : null,
  unit_cost: stocked && seesCosts ? orNull(start.unitCost) : null,
  bin: trimmedBin(start.bin),
});

/**
 * What a new material or base starts with: its picture, and at a location,
 * the main one unless another is picked, its bin and, when it keeps stock
 * (`stocked`), its starting stock. The unit cost is asked only of a member who
 * sees costs.
 */
export function StartFields({
  value,
  onChange,
  unit,
  stocked = true,
}: {
  value: Start;
  onChange: (start: Start) => void;
  /** The unit it's counted in, beside its starting quantity. */
  unit: string;
  stocked?: boolean;
}) {
  const { t } = useTranslation("catalog");
  const seesCosts = useSeesCosts();
  const change = (part: Partial<Start>) => onChange({ ...value, ...part });
  const pick = (file: File) => {
    const refused = pictureRefused(file, t);
    if (refused) return toast.error(refused);
    change({ picture: file });
  };
  return (
    <>
      <Field label={t("items.picture")} hint={value.picture?.name}>
        <FileDropArea
          accept={IMAGE_TYPES.join(",")}
          onFile={pick}
          prompt={t(value.picture ? "items.pictureReplace" : "items.picturePrompt")}
        />
      </Field>
      <fieldset className="flex flex-col gap-4">
        <legend className="mb-2 text-sm font-medium">{t("items.start.title")}</legend>
        <div className="grid grid-cols-pair gap-4">
          <LocationField
            value={value.locationId}
            onChange={(locationId) => change({ locationId })}
          />
          {stocked && (
            <Field label={t("items.start.quantity", { unit })}>
              <Input
                inputMode="decimal"
                value={value.quantity}
                onChange={(event) => change({ quantity: event.target.value })}
              />
            </Field>
          )}
          {stocked && seesCosts && (
            <Field label={t("hoard:common.unitCost")} hint={t("items.start.unitCostHint")}>
              <Input
                inputMode="decimal"
                value={value.unitCost}
                onChange={(event) => change({ unitCost: event.target.value })}
              />
            </Field>
          )}
        </div>
        <BinFields value={value.bin} onChange={(bin) => change({ bin })} />
      </fieldset>
    </>
  );
}
