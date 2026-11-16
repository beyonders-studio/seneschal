import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import type { OrderRead } from "@/api/generated/hoard.schemas";
import {
  getListOrdersQueryKey,
  getReadOrderQueryKey,
  useCreateOrder,
  useUpdateOrder,
} from "@/api/generated/orders/orders";
import { toast } from "@/components/Toaster";
import { LocationField } from "@/features/adjustments/fields";
import { today } from "@/features/adjustments/labels";
import { ContactPicker } from "@/features/contacts/ContactPicker";
import { errorMessage } from "@/lib/errors";
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
import { Textarea } from "@/ui/textarea";

/** The money an order holds beside its lines. */
const AMOUNTS = ["discount", "shipping", "tax"] as const;

/**
 * Adds an order by hand, or changes `order`: its code, dates, customer (a
 * store's order has none), location, delivery address, whether it is paid,
 * and its discount, shipping and tax. Closing it drops what was typed.
 * Answers the order saved.
 */
export function OrderDialog({
  open,
  onOpenChange,
  order,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  order?: OrderRead;
  onSaved?: (saved: OrderRead) => void;
}) {
  const { t } = useTranslation("orders");
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{order ? t("order.editTitle") : t("order.addTitle")}</DialogTitle>
        </DialogHeader>
        <OrderForm
          order={order}
          onSaved={(saved) => {
            onOpenChange(false);
            onSaved?.(saved);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

function OrderForm({ order, onSaved }: { order?: OrderRead; onSaved: (saved: OrderRead) => void }) {
  const { t } = useTranslation("orders");
  const queries = useQueryClient();
  const fromStore = order?.store_id != null;
  const [code, setCode] = useState(order?.code ?? "");
  const [orderedOn, setOrderedOn] = useState(order?.ordered_on ?? today());
  const [dueOn, setDueOn] = useState(order?.due_on ?? "");
  const [customer, setCustomer] = useState(order?.customer_contact_id ?? null);
  const [location, setLocation] = useState(order?.location_id ?? null);
  const [address, setAddress] = useState(order?.delivery_address ?? "");
  const [paid, setPaid] = useState(order?.paid ?? false);
  const [amounts, setAmounts] = useState(() =>
    Object.fromEntries(AMOUNTS.map((name) => [name, order?.[name] ?? ""]))
  );
  const create = useCreateOrder();
  const update = useUpdateOrder();
  const pending = create.isPending || update.isPending;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const fields = {
      ...(code.trim() ? { code: code.trim() } : {}),
      ordered_on: orderedOn,
      due_on: dueOn || null,
      ...(location === null ? {} : { location_id: location }),
      delivery_address: address.trim() || null,
      paid,
      ...Object.fromEntries(AMOUNTS.map((name) => [name, amounts[name] || "0"])),
    };
    const saving = order
      ? () =>
          update.mutateAsync({
            orderId: order.id,
            data: { ...fields, ...(customer === null ? {} : { customer_contact_id: customer }) },
          })
      : customer === null
        ? null
        : () => create.mutateAsync({ data: { ...fields, customer_contact_id: customer } });
    if (!saving) {
      toast.error(t("customers.required"));
      return;
    }
    try {
      const saved = await saving();
      queries.setQueryData(getReadOrderQueryKey(saved.id), saved);
      void queries.invalidateQueries({ queryKey: getListOrdersQueryKey() });
      onSaved(saved);
    } catch (error) {
      toast.error(errorMessage(error, t, "order.saveFailed"));
    }
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogBody className="space-y-4">
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("order.code")} hint={order ? undefined : t("order.codeHint")}>
            <Input value={code} onChange={(event) => setCode(event.target.value)} />
          </Field>
          <Field label={t("order.orderedOn")}>
            <Input
              type="date"
              required
              value={orderedOn}
              onChange={(event) => setOrderedOn(event.target.value)}
            />
          </Field>
          <Field label={t("order.dueOn")}>
            <Input type="date" value={dueOn} onChange={(event) => setDueOn(event.target.value)} />
          </Field>
        </div>
        {!fromStore && <ContactPicker of="customers" value={customer} onChange={setCustomer} />}
        <LocationField value={location} onChange={setLocation} />
        <Field label={t("order.deliveryAddress")}>
          <Textarea value={address} onChange={(event) => setAddress(event.target.value)} />
        </Field>
        <div className="grid grid-cols-pair gap-4">
          {AMOUNTS.map((name) => (
            <Field key={name} label={t(`order.${name}`)}>
              <Input
                inputMode="decimal"
                value={amounts[name]}
                onChange={(event) => setAmounts({ ...amounts, [name]: event.target.value })}
              />
            </Field>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <Checkbox
            id="order-paid"
            checked={paid}
            onCheckedChange={(checked) => setPaid(checked === true)}
          />
          <Label htmlFor="order-paid">{t("order.paid")}</Label>
        </div>
      </DialogBody>
      <DialogFooter>
        <Button type="submit" disabled={pending}>
          {pending ? t("hoard:common.saving") : t("hoard:common.save")}
        </Button>
      </DialogFooter>
    </form>
  );
}
