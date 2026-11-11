import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import type { PurchaseRead } from "@/api/generated/hoard.schemas";
import {
  getListPurchasesQueryKey,
  getReadPurchaseQueryKey,
  useCreatePurchase,
  useUpdatePurchase,
} from "@/api/generated/purchases/purchases";
import { toast } from "@/components/Toaster";
import { LocationField } from "@/features/adjustments/fields";
import { today } from "@/features/adjustments/labels";
import { SupplierPicker } from "@/features/contacts/SupplierPicker";
import { errorMessage } from "@/lib/errors";
import { useSeesCosts } from "@/lib/permissions";
import { Button } from "@/ui/button";
import { Checkbox } from "@/ui/checkbox";
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
import { Label } from "@/ui/label";

/** The money a purchase holds beside its lines. */
const COSTS = ["discount", "shipping", "tax"] as const;

/**
 * Adds a purchase, or changes `purchase`: its code, date, supplier, location,
 * whether it is paid, and, for a member who sees costs, its discount,
 * shipping and tax. Closing it drops what was typed. Answers the purchase saved.
 */
export function PurchaseDialog({
  open,
  onOpenChange,
  purchase,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  purchase?: PurchaseRead;
  onSaved?: (saved: PurchaseRead) => void;
}) {
  const { t } = useTranslation("movements");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{purchase ? t("purchases.editTitle") : t("purchases.addTitle")}</DialogTitle>
        </DialogHeader>
        <PurchaseForm
          purchase={purchase}
          onSaved={(saved) => {
            onOpenChange(false);
            onSaved?.(saved);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

function PurchaseForm({
  purchase,
  onSaved,
}: {
  purchase?: PurchaseRead;
  onSaved: (saved: PurchaseRead) => void;
}) {
  const { t } = useTranslation("movements");
  const seesCosts = useSeesCosts();
  const queries = useQueryClient();
  const [code, setCode] = useState(purchase?.code ?? "");
  const [datedOn, setDatedOn] = useState(purchase?.dated_on ?? today());
  const [supplier, setSupplier] = useState(purchase?.supplier_contact_id ?? null);
  const [location, setLocation] = useState(purchase?.location_id ?? null);
  const [paid, setPaid] = useState(purchase?.paid ?? false);
  const [costs, setCosts] = useState(() =>
    Object.fromEntries(COSTS.map((name) => [name, purchase?.[name] ?? ""]))
  );
  const create = useCreatePurchase();
  const update = useUpdatePurchase();
  const saving = create.isPending || update.isPending;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const fields = {
      ...(code.trim() ? { code: code.trim() } : {}),
      dated_on: datedOn,
      supplier_contact_id: supplier,
      ...(location === null ? {} : { location_id: location }),
      paid,
      ...(seesCosts
        ? Object.fromEntries(COSTS.map((name) => [name, costs[name] || "0"]))
        : {}),
    };
    try {
      const saved = purchase
        ? await update.mutateAsync({ purchaseId: purchase.id, data: fields })
        : await create.mutateAsync({ data: fields });
      queries.setQueryData(getReadPurchaseQueryKey(saved.id), saved);
      void queries.invalidateQueries({ queryKey: getListPurchasesQueryKey() });
      onSaved(saved);
    } catch (error) {
      toast.error(errorMessage(error, t, "purchases.saveFailed"));
    }
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogBody className="space-y-4">
        <div className="grid-cols-pair grid gap-4">
          <Field label={t("purchases.code")} hint={purchase ? undefined : t("purchases.codeHint")}>
            <Input value={code} onChange={(event) => setCode(event.target.value)} />
          </Field>
          <Field label={t("hoard:common.date")}>
            <Input
              type="date"
              required
              value={datedOn}
              onChange={(event) => setDatedOn(event.target.value)}
            />
          </Field>
        </div>
        <SupplierPicker value={supplier} onChange={setSupplier} />
        <LocationField value={location} onChange={setLocation} />
        {seesCosts && (
          <div className="grid-cols-pair grid gap-4">
            {COSTS.map((name) => (
              <Field key={name} label={t(`purchases.${name}`)}>
                <Input
                  inputMode="decimal"
                  value={costs[name]}
                  onChange={(event) => setCosts({ ...costs, [name]: event.target.value })}
                />
              </Field>
            ))}
          </div>
        )}
        <div className="flex items-center gap-2">
          <Checkbox
            id="purchase-paid"
            checked={paid}
            onCheckedChange={(checked) => setPaid(checked === true)}
          />
          <Label htmlFor="purchase-paid">{t("purchases.paid")}</Label>
        </div>
      </DialogBody>
      <DialogFooter>
        <Button type="submit" disabled={saving}>
          {saving ? t("hoard:common.saving") : t("hoard:common.save")}
        </Button>
      </DialogFooter>
    </form>
  );
}
