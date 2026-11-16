import { X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useListBases } from "@/api/generated/bases/bases";
import type { ItemKind, ItemStatus, ProductDetail } from "@/api/generated/hoard.schemas";
import { useListMaterials } from "@/api/generated/materials/materials";
import { readProduct, useListProducts } from "@/api/generated/products/products";
import { useListSettingsLocations } from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { IdSelect } from "@/features/items/IdSelect";
import { useVariationName } from "@/features/products/variations";
import { IncludeArchived } from "@/features/search/records";
import { useLocationName } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
import { Button } from "@/ui/button";
import { Combobox, type ComboboxItem } from "@/ui/combobox";
import { Field } from "@/ui/field";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";

/** The most materials, bases and products the item picker lists, each, for a search. */
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

/** Every kind of item. */
const EVERY_KIND: readonly ItemKind[] = ["material", "base", "variation"];
/** The kinds a purchase buys. */
const BOUGHT_KINDS: readonly ItemKind[] = ["material", "variation"];

/**
 * An item to move: a material, a base or a product's variation, of `kinds`;
 * or, with `bought`, what a purchase buys: any material, one that keeps no
 * stock included, or a variation of a product that is not a bundle. Without
 * `bundles`, a bundle's variations are left out, a bundle having no stock of
 * its own; they are offered unless `bought`. A product
 * with several variations is picked first, then one of them; a product with
 * one is that one. `chosen` names `value` when the field opens with it set;
 * `clearLabel` offers picking none, as that button's name. Archived items
 * are left out unless asked for: by the field's own switch, or by
 * `archived` from a switch its caller holds for several fields.
 */
export function ItemField({
  label,
  value,
  onChange,
  chosen = null,
  bought = false,
  kinds = bought ? BOUGHT_KINDS : EVERY_KIND,
  bundles = !bought,
  clearLabel,
  archived,
}: {
  label: string;
  value: number | null;
  onChange: (itemId: number | null) => void;
  chosen?: string | null;
  bought?: boolean;
  kinds?: readonly ItemKind[];
  bundles?: boolean;
  clearLabel?: string;
  archived?: boolean;
}) {
  const { t } = useTranslation("movements");
  const variationName = useVariationName();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<ComboboxItem | null>(
    value === null ? null : { value: `m${value}`, label: chosen ?? "" }
  );
  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [ownArchived, setOwnArchived] = useState(false);
  const withArchived = archived ?? ownArchived;
  const params = {
    q: search || undefined,
    limit: MATCHES,
    ...(withArchived && { archived: true }),
  };
  const archivedMark = (status: ItemStatus) =>
    status === "archived" ? t("catalog:items.statuses.archived") : undefined;
  const found = useListMaterials(params, {
    query: { enabled: open && kinds.includes("material") },
  });
  const bases = useListBases(params, { query: { enabled: open && kinds.includes("base") } });
  const products = useListProducts(params, {
    query: { enabled: open && kinds.includes("variation") },
  });
  const choices: ComboboxItem[] = [
    ...(found.data?.items ?? [])
      .filter((material) => bought || material.inventoriable)
      .map((material) => ({
        value: `m${material.id}`,
        label: material.name,
        hint: t("item.kinds.material"),
        sublabel: archivedMark(material.status),
      })),
    ...(bases.data?.items ?? []).map((base) => ({
      value: `b${base.id}`,
      label: base.name,
      hint: t("item.kinds.base"),
      sublabel: archivedMark(base.status),
    })),
    ...(products.data?.items ?? [])
      .filter((one) => bundles || !one.bundle)
      .map((one) => ({
        value: `p${one.id}`,
        label: one.name,
        hint: t("item.kinds.variation"),
        sublabel: archivedMark(one.status),
      })),
  ];

  const choose = async (next: string) => {
    const choice = choices.find((one) => one.value === next);
    if (!choice) return;
    setPicked(choice);
    setProduct(null);
    const id = Number(next.slice(1));
    if (!next.startsWith("p")) return onChange(id);
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
            loading={found.isLoading || bases.isLoading || products.isLoading}
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
      {archived === undefined && (
        <IncludeArchived checked={ownArchived} onCheckedChange={setOwnArchived} />
      )}
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
