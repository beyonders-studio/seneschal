import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type HoardPropertyValueInput,
  ItemStatus,
  type MaterialCreate,
  type MaterialDetail,
} from "@/api/generated/hoard.schemas";
import {
  createMaterial,
  getListMaterialsQueryKey,
  getReadMaterialQueryKey,
  readMaterial,
  updateMaterial,
} from "@/api/generated/materials/materials";
import { useReadUnitConversion } from "@/api/generated/settings/settings";
import { SupplierPicker } from "@/features/contacts/SupplierPicker";
import { saveMoved, usePagePlaces, useTypeChoice } from "@/features/fields/records";
import { orNull } from "@/features/items/format";
import { IdSelect } from "@/features/items/IdSelect";
import { StatusField } from "@/features/items/ItemList";
import { withPicture } from "@/features/items/ItemPicture";
import { NO_START, StartFields, startBody } from "@/features/items/start";
import { TypePicker } from "@/features/items/TypePicker";
import { UnitPicker } from "@/features/items/UnitPicker";
import { MATERIALS } from "@/features/materials/fields";
import { useSettingsNames } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
import { useSeesCosts } from "@/lib/permissions";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/ui/dialog";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";
import { Switch } from "@/ui/switch";
import { Textarea } from "@/ui/textarea";

/**
 * Adding a material, with its picture, bin and starting stock, or editing
 * `material`, in a dialog. Closing it drops what was typed; saving answers the
 * material as Hoard keeps it.
 */
export function MaterialDialog({
  material,
  open,
  onOpenChange,
  onSaved,
}: {
  material?: MaterialDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved?: (material: MaterialDetail) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {open && (
          <MaterialForm
            material={material}
            onSaved={(saved) => {
              onOpenChange(false);
              onSaved?.(saved);
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function MaterialForm({
  material,
  onSaved,
}: {
  material?: MaterialDetail;
  onSaved: (material: MaterialDetail) => void;
}) {
  const { t } = useTranslation("catalog");
  const queryClient = useQueryClient();
  const seesCosts = useSeesCosts();
  const { units, unit, categoriesOf } = useSettingsNames();
  const type = useTypeChoice(MATERIALS, material);
  // Asked for only where the material's page shows it, or once it has one.
  const places = usePagePlaces(MATERIALS);
  const [gtin, setGtin] = useState(material?.gtin ?? "");
  const asksGtin = Boolean(material?.gtin) || places(type.typeId ?? undefined, "gtin");
  const [name, setName] = useState(material?.name ?? "");
  const [status, setStatus] = useState<ItemStatus>(material?.status ?? ItemStatus.active);
  const [notes, setNotes] = useState(material?.notes ?? "");
  const [start, setStart] = useState(NO_START);
  const [sku, setSku] = useState(material?.sku ?? "");
  const [categoryId, setCategoryId] = useState(material?.category_id ?? null);
  const [trackingUnitId, setTrackingUnitId] = useState(material?.tracking_unit_id ?? null);
  const [purchaseUnitId, setPurchaseUnitId] = useState(material?.purchase_unit_id ?? null);
  const [conversion, setConversion] = useState(material?.purchase_conversion ?? "1");
  const [unitWeight, setUnitWeight] = useState(material?.unit_weight ?? "");
  const pair = { from_unit_id: purchaseUnitId ?? 0, to_unit_id: trackingUnitId ?? 0 };
  const known = useReadUnitConversion(pair, {
    query: { enabled: purchaseUnitId !== null && trackingUnitId !== null },
  }).data?.factor;
  const weighed = units.find((unit) => unit.id === trackingUnitId)?.measure === "mass";
  const [lowStockLimit, setLowStockLimit] = useState(material?.low_stock_limit ?? "");
  const [inventoriable, setInventoriable] = useState(material?.inventoriable ?? true);
  const [supplierId, setSupplierId] = useState(material?.supplier_contact_id ?? null);
  const [supplierCode, setSupplierCode] = useState(material?.supplier_code ?? "");
  const save = useMutation({
    mutationFn: async ({
      body,
      moved,
    }: {
      body: MaterialCreate;
      moved: HoardPropertyValueInput[];
    }) => {
      if (material) {
        const saved = await updateMaterial(material.id, body);
        await saveMoved("material", material.id, moved);
        return moved.length ? readMaterial(material.id) : saved;
      }
      const made = await createMaterial(body);
      return start.picture ? withPicture(made, start.picture, t) : made;
    },
    onSuccess: async (saved) => {
      queryClient.setQueryData(getReadMaterialQueryKey(saved.id), saved);
      await queryClient.invalidateQueries({ queryKey: getListMaterialsQueryKey() });
      onSaved(saved);
    },
  });

  const chooseCategory = (next: number | null) => {
    setCategoryId(next);
    type.category(next);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (trackingUnitId === null) return;
    const body: MaterialCreate = {
      name: name.trim(),
      status,
      notes: orNull(notes),
      sku: orNull(sku),
      ...(asksGtin ? { gtin: orNull(gtin) } : {}),
      category_id: categoryId,
      tracking_unit_id: trackingUnitId,
      purchase_unit_id: purchaseUnitId,
      purchase_conversion: purchaseUnitId === null ? "1" : (known ?? conversion),
      unit_weight: weighed ? null : orNull(unitWeight),
      low_stock_limit: orNull(lowStockLimit),
      inventoriable,
      supplier_contact_id: supplierId,
      supplier_code: orNull(supplierCode),
      ...type.body,
      ...(material ? {} : { start: startBody(start, inventoriable, seesCosts) }),
    };
    type.move((moved) => save.mutate({ body, moved }));
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>{material ? t("materials.edit") : t("materials.add")}</DialogTitle>
      </DialogHeader>
      <DialogBody className="flex flex-col gap-4">
        <Field label={t("hoard:common.name")}>
          <Input value={name} onChange={(event) => setName(event.target.value)} required />
        </Field>
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("hoard:common.sku")}>
            <Input value={sku} onChange={(event) => setSku(event.target.value)} />
          </Field>
          {asksGtin && (
            <Field label={t("items.gtin")} hint={t("items.gtinHint")}>
              <Input
                value={gtin}
                inputMode="numeric"
                autoComplete="off"
                onChange={(event) => setGtin(event.target.value)}
              />
            </Field>
          )}
          <Field label={t("hoard:common.category")}>
            <IdSelect
              value={categoryId}
              options={categoriesOf("material")}
              onChange={chooseCategory}
              none={t("hoard:common.none")}
            />
          </Field>
          <Field label={t("items.type")}>
            <TypePicker kind="material" value={type.typeId} onChange={type.choose} />
          </Field>
          <Field label={t("materials.trackingUnit")}>
            <UnitPicker value={trackingUnitId} onChange={setTrackingUnitId} />
          </Field>
          <Field label={t("materials.purchaseUnit")}>
            <UnitPicker
              value={purchaseUnitId}
              onChange={setPurchaseUnitId}
              none={t("materials.sameUnit")}
            />
          </Field>
          {purchaseUnitId !== null && (
            <Field
              label={t("materials.conversion")}
              hint={known ? t("materials.conversionKnown") : t("materials.conversionHint")}
            >
              <Input
                inputMode="decimal"
                value={known ?? conversion}
                onChange={(event) => setConversion(event.target.value)}
                disabled={!!known}
                required
              />
            </Field>
          )}
          {trackingUnitId !== null && !weighed && (
            <Field label={t("materials.unitWeight")} hint={t("materials.unitWeightHint")}>
              <Input
                inputMode="decimal"
                value={unitWeight}
                onChange={(event) => setUnitWeight(event.target.value)}
              />
            </Field>
          )}
          <Field label={t("hoard:common.lowStockLimit")}>
            <Input
              inputMode="decimal"
              value={lowStockLimit}
              onChange={(event) => setLowStockLimit(event.target.value)}
            />
          </Field>
          <SupplierPicker
            label={t("materials.supplier")}
            value={supplierId}
            onChange={setSupplierId}
          />
          <Field label={t("materials.supplierCode")}>
            <Input value={supplierCode} onChange={(event) => setSupplierCode(event.target.value)} />
          </Field>
          {material && <StatusField value={status} onChange={setStatus} />}
        </div>
        <Field label={t("materials.inventoriable")} hint={t("materials.inventoriableHint")}>
          <Switch checked={inventoriable} onCheckedChange={setInventoriable} />
        </Field>
        <Field label={t("hoard:common.notes")}>
          <Textarea value={notes} onChange={(event) => setNotes(event.target.value)} />
        </Field>
        {!material && (
          <StartFields
            value={start}
            onChange={setStart}
            unit={unit(trackingUnitId)}
            stocked={inventoriable}
          />
        )}
        {save.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(save.error, t, "materials.saveFailed")}
          </p>
        )}
        {type.dialog}
      </DialogBody>
      <DialogFooter
        submitLabel={t("hoard:common.save")}
        pending={save.isPending}
        pendingLabel={t("hoard:common.saving")}
        disabled={!name.trim() || trackingUnitId === null}
      />
    </form>
  );
}
