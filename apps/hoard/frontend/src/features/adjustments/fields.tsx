import { X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { ProductDetail } from "@/api/generated/hoard.schemas";
import { useListMaterials } from "@/api/generated/materials/materials";
import { readProduct, useListProducts } from "@/api/generated/products/products";
import { useListSettingsLocations } from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { IdSelect } from "@/features/items/IdSelect";
import { useVariationName } from "@/features/products/variations";
import { useLocationName } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
import { Button } from "@/ui/button";
import { Combobox, type ComboboxItem } from "@/ui/combobox";
import { Field } from "@/ui/field";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";

/** The most materials, and the most products, the item picker lists for a search. */
const MATCHES = 50;

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
  const locations = useListSettingsLocations().data ?? [];
  const locationName = useLocationName();
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
              {locationName(place)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  );
}

/**
 * An item to move: a material or a product's variation, or any material with
 * `materials`. A product with several variations is picked first, then one of
 * them; a product with one is that one. `chosen` names `value` when the field
 * opens with it set; `clearLabel` offers picking none, as that button's name.
 */
export function ItemField({
  label,
  value,
  onChange,
  chosen = null,
  materials = false,
  clearLabel,
}: {
  label: string;
  value: number | null;
  onChange: (itemId: number | null) => void;
  chosen?: string | null;
  materials?: boolean;
  clearLabel?: string;
}) {
  const { t } = useTranslation("movements");
  const variationName = useVariationName();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<ComboboxItem | null>(
    value === null ? null : { value: `m${value}`, label: chosen ?? "" }
  );
  const [product, setProduct] = useState<ProductDetail | null>(null);
  const params = { q: search || undefined, limit: MATCHES };
  const found = useListMaterials(params, { query: { enabled: open } });
  const products = useListProducts(params, { query: { enabled: open && !materials } });
  const choices: ComboboxItem[] = [
    ...(found.data?.items ?? [])
      .filter((material) => materials || material.inventoriable)
      .map((material) => ({
        value: `m${material.id}`,
        label: material.name,
        hint: t("item.kinds.material"),
      })),
    ...(products.data?.items ?? []).map((one) => ({
      value: `p${one.id}`,
      label: one.name,
      hint: t("item.kinds.variation"),
    })),
  ];

  const choose = async (next: string) => {
    const choice = choices.find((one) => one.value === next);
    if (!choice) return;
    setPicked(choice);
    setProduct(null);
    const id = Number(next.slice(1));
    if (next.startsWith("m")) return onChange(id);
    onChange(null);
    try {
      const detail = await readProduct(id);
      if (detail.variations.length === 1) onChange(detail.variations[0].id);
      else setProduct(detail);
    } catch (error) {
      toast.error(errorMessage(error, t, "item.loadFailed"));
    }
  };

  const clear = () => {
    setPicked(null);
    setProduct(null);
    onChange(null);
  };

  return (
    <>
      <div className="flex items-end gap-1">
        <Field label={label}>
          <Combobox
            items={choices}
            onSearchChange={setSearch}
            onOpenChange={setOpen}
            loading={found.isLoading || products.isLoading}
            loadingLabel={t("hoard:common.loading")}
            value={picked?.value ?? null}
            selectedLabel={picked?.label ?? null}
            onValueChange={(next) => void choose(next)}
            placeholder={t("item.choose")}
            searchPlaceholder={t("hoard:common.search")}
            emptyMessage={t("item.none")}
          />
        </Field>
        {clearLabel && picked && (
          <Button variant="ghost" size="icon" aria-label={clearLabel} onClick={clear}>
            <X aria-hidden />
          </Button>
        )}
      </div>
      {product && (
        <Field label={t("catalog:products.variation")}>
          <IdSelect
            value={value}
            options={product.variations.map((variation) => ({
              id: variation.id,
              name: variationName(variation),
            }))}
            onChange={onChange}
          />
        </Field>
      )}
    </>
  );
}
