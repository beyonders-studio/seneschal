import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import type { MaterialDetail, MaterialWrite } from "@/api/generated/hoard.schemas";
import {
  createMaterial,
  getListMaterialsQueryKey,
  getReadMaterialQueryKey,
  updateMaterial,
} from "@/api/generated/materials/materials";
import { SupplierPicker } from "@/features/contacts/SupplierPicker";
import { orNull } from "@/features/items/format";
import { IdSelect } from "@/features/items/IdSelect";
import { useSettingsNames } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
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

/**
 * Adding a material, or editing `material`, in a dialog. Closing it drops
 * what was typed; saving answers the material as Hoard keeps it.
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
  const { units, categoriesOf } = useSettingsNames();
  const [name, setName] = useState(material?.name ?? "");
  const [sku, setSku] = useState(material?.sku ?? "");
  const [categoryId, setCategoryId] = useState(material?.category_id ?? null);
  const [trackingUnitId, setTrackingUnitId] = useState(material?.tracking_unit_id ?? null);
  const [purchaseUnitId, setPurchaseUnitId] = useState(material?.purchase_unit_id ?? null);
  const [conversion, setConversion] = useState(material?.purchase_conversion ?? "1");
  const [lowStockLimit, setLowStockLimit] = useState(material?.low_stock_limit ?? "");
  const [inventoriable, setInventoriable] = useState(material?.inventoriable ?? true);
  const [supplierId, setSupplierId] = useState(material?.supplier_contact_id ?? null);
  const [supplierCode, setSupplierCode] = useState(material?.supplier_code ?? "");
  const save = useMutation({
    mutationFn: (body: MaterialWrite) =>
      material ? updateMaterial(material.id, body) : createMaterial(body),
    onSuccess: async (saved) => {
      queryClient.setQueryData(getReadMaterialQueryKey(saved.id), saved);
      await queryClient.invalidateQueries({ queryKey: getListMaterialsQueryKey() });
      onSaved(saved);
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (trackingUnitId === null) return;
    save.mutate({
      name: name.trim(),
      sku: orNull(sku),
      category_id: categoryId,
      tracking_unit_id: trackingUnitId,
      purchase_unit_id: purchaseUnitId,
      purchase_conversion: purchaseUnitId === null ? "1" : conversion,
      low_stock_limit: orNull(lowStockLimit),
      inventoriable,
      supplier_contact_id: supplierId,
      supplier_code: orNull(supplierCode),
    });
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
          <Field label={t("hoard:common.category")}>
            <IdSelect
              value={categoryId}
              options={categoriesOf("material")}
              onChange={setCategoryId}
              none={t("hoard:common.none")}
            />
          </Field>
          <Field
            label={t("materials.trackingUnit")}
            hint={units.length === 0 ? t("materials.noUnits") : undefined}
          >
            <IdSelect value={trackingUnitId} options={units} onChange={setTrackingUnitId} />
          </Field>
          <Field label={t("materials.purchaseUnit")}>
            <IdSelect
              value={purchaseUnitId}
              options={units}
              onChange={setPurchaseUnitId}
              none={t("materials.sameUnit")}
            />
          </Field>
          {purchaseUnitId !== null && (
            <Field label={t("materials.conversion")} hint={t("materials.conversionHint")}>
              <Input
                inputMode="decimal"
                value={conversion}
                onChange={(event) => setConversion(event.target.value)}
                required
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
        </div>
        <Field label={t("materials.inventoriable")} hint={t("materials.inventoriableHint")}>
          <Switch checked={inventoriable} onCheckedChange={setInventoriable} />
        </Field>
        {save.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(save.error, t, "materials.saveFailed")}
          </p>
        )}
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
