import { type FormEvent, type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  OrderRead,
  OrderShipmentLineRead,
  OrderShipmentRead,
} from "@/api/generated/hoard.schemas";
import {
  useCreateShipment,
  useCreateShipmentReturn,
  useShipShipment,
  useUpdateShipment,
} from "@/api/generated/shipments/shipments";
import { toast } from "@/components/Toaster";
import { LocationField } from "@/features/adjustments/fields";
import { today, useMovementLabels } from "@/features/adjustments/labels";
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

/** What a dialog on an order's shipments is for. */
export type ShipmentAction =
  | { kind: "add" }
  | { kind: "edit"; shipment: OrderShipmentRead }
  | { kind: "ship"; shipment: OrderShipmentRead }
  | { kind: "return"; shipment: OrderShipmentRead; line: OrderShipmentLineRead };

/** What is left of each line of `order` that ships, once its shipments are counted. */
export const leftToShip = (order: OrderRead) => {
  const bundles = new Set(order.lines.map((line) => line.bundle_line_id));
  return order.lines
    .filter((line) => !bundles.has(line.id))
    .map((line) => ({ line, left: Number(line.quantity) - Number(line.held) }))
    .filter(({ left }) => left > 0);
};

/**
 * The dialog `action` opens on `order`'s shipments: planning one, changing
 * one, shipping one on a date, or taking a return of one of its lines.
 * Closing it drops what was typed; `onSaved` takes the order as it is after.
 */
export function ShipmentDialog({
  order,
  action,
  onClose,
  onSaved,
}: {
  order: OrderRead;
  action: ShipmentAction | null;
  onClose: () => void;
  onSaved: (saved: OrderRead) => void;
}) {
  const { t } = useTranslation("orders");
  const done = (saved: OrderRead) => {
    onClose();
    onSaved(saved);
  };
  let form: ReactNode = null;
  if (action?.kind === "add") form = <PlanForm order={order} onSaved={done} />;
  else if (action?.kind === "edit")
    form = <DetailsForm shipment={action.shipment} onSaved={done} />;
  else if (action?.kind === "ship") form = <ShipForm shipment={action.shipment} onSaved={done} />;
  else if (action?.kind === "return")
    form = <ReturnForm shipment={action.shipment} line={action.line} onSaved={done} />;
  return (
    <Dialog open={action !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{action ? t(`shipments.${action.kind}Title`) : null}</DialogTitle>
        </DialogHeader>
        {form}
      </DialogContent>
    </Dialog>
  );
}

function DialogForm({
  onSubmit,
  saving,
  submitLabel,
  children,
}: {
  onSubmit: () => Promise<void>;
  saving: boolean;
  submitLabel?: string;
  children: ReactNode;
}) {
  const { t } = useTranslation("orders");
  return (
    <form
      className="contents"
      onSubmit={(event: FormEvent) => {
        event.preventDefault();
        void onSubmit();
      }}
    >
      <DialogBody className="space-y-4">{children}</DialogBody>
      <DialogFooter>
        <Button type="submit" disabled={saving}>
          {saving ? t("hoard:common.saving") : (submitLabel ?? t("hoard:common.save"))}
        </Button>
      </DialogFooter>
    </form>
  );
}

/** A carrier, a tracking number, a reference and a delivery address, as typed. */
function useDetails(shipment?: OrderShipmentRead) {
  const [carrier, setCarrier] = useState(shipment?.carrier_contact_id ?? null);
  const [tracking, setTracking] = useState(shipment?.tracking ?? "");
  const [reference, setReference] = useState(shipment?.reference ?? "");
  const [address, setAddress] = useState(shipment?.delivery_address ?? "");
  return {
    values: {
      carrier_contact_id: carrier,
      tracking: tracking.trim() || null,
      reference: reference.trim() || null,
      delivery_address: address.trim() || null,
    },
    fields: (
      <DetailsFields
        carrier={[carrier, setCarrier]}
        tracking={[tracking, setTracking]}
        reference={[reference, setReference]}
        address={[address, setAddress]}
      />
    ),
  };
}

type Typed = [string, (value: string) => void];

function DetailsFields({
  carrier,
  tracking,
  reference,
  address,
}: {
  carrier: [number | null, (contactId: number | null) => void];
  tracking: Typed;
  reference: Typed;
  address: Typed;
}) {
  const { t } = useTranslation("orders");
  return (
    <>
      <ContactPicker of="carriers" value={carrier[0]} onChange={carrier[1]} />
      <div className="grid grid-cols-pair gap-4">
        <Field label={t("shipments.tracking")}>
          <Input value={tracking[0]} onChange={(event) => tracking[1](event.target.value)} />
        </Field>
        <Field label={t("shipments.reference")}>
          <Input value={reference[0]} onChange={(event) => reference[1](event.target.value)} />
        </Field>
      </div>
      <Field label={t("order.deliveryAddress")} hint={t("shipments.addressHint")}>
        <Textarea value={address[0]} onChange={(event) => address[1](event.target.value)} />
      </Field>
    </>
  );
}

function PlanForm({ order, onSaved }: { order: OrderRead; onSaved: (saved: OrderRead) => void }) {
  const { t } = useTranslation("orders");
  const { item, quantity } = useMovementLabels();
  const left = leftToShip(order);
  const [location, setLocation] = useState<number | null>(order.location_id);
  const [sending, setSending] = useState(() =>
    Object.fromEntries(left.map(({ line, left: rest }) => [line.id, String(rest)]))
  );
  const details = useDetails();
  const create = useCreateShipment();

  const submit = async () => {
    const lines = left
      .filter(({ line }) => Number(sending[line.id]) > 0)
      .map(({ line }) => ({ order_line_id: line.id, quantity: sending[line.id] }));
    if (lines.length === 0) {
      toast.error(t("shipments.nothingPicked"));
      return;
    }
    try {
      onSaved(
        await create.mutateAsync({
          data: {
            order_id: order.id,
            ...(location === null ? {} : { location_id: location }),
            lines,
            ...details.values,
          },
        })
      );
    } catch (error) {
      toast.error(errorMessage(error, t, "shipments.saveFailed"));
    }
  };

  return (
    <DialogForm onSubmit={submit} saving={create.isPending}>
      <LocationField value={location} onChange={setLocation} />
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">{t("shipments.sending")}</legend>
        {left.map(({ line, left: rest }) => (
          <Field
            key={line.id}
            label={item(line.item)}
            hint={t("shipments.left", { quantity: quantity(String(rest)) })}
          >
            <Input
              inputMode="decimal"
              value={sending[line.id]}
              onChange={(event) => setSending({ ...sending, [line.id]: event.target.value })}
            />
          </Field>
        ))}
      </fieldset>
      {details.fields}
    </DialogForm>
  );
}

function DetailsForm({
  shipment,
  onSaved,
}: {
  shipment: OrderShipmentRead;
  onSaved: (saved: OrderRead) => void;
}) {
  const { t } = useTranslation("orders");
  const [location, setLocation] = useState<number | null>(shipment.location_id);
  const details = useDetails(shipment);
  const update = useUpdateShipment();
  const pending = shipment.status === "pending";

  const submit = async () => {
    try {
      onSaved(
        await update.mutateAsync({
          shipmentId: shipment.id,
          data: {
            ...details.values,
            ...(pending && location !== null && location !== shipment.location_id
              ? { location_id: location }
              : {}),
          },
        })
      );
    } catch (error) {
      toast.error(errorMessage(error, t, "shipments.saveFailed"));
    }
  };

  return (
    <DialogForm onSubmit={submit} saving={update.isPending}>
      {pending && <LocationField value={location} onChange={setLocation} />}
      {details.fields}
    </DialogForm>
  );
}

function ShipForm({
  shipment,
  onSaved,
}: {
  shipment: OrderShipmentRead;
  onSaved: (saved: OrderRead) => void;
}) {
  const { t } = useTranslation("orders");
  const [shippedOn, setShippedOn] = useState(today());
  const ship = useShipShipment();

  const submit = async () => {
    try {
      onSaved(await ship.mutateAsync({ shipmentId: shipment.id, data: { shipped_on: shippedOn } }));
    } catch (error) {
      toast.error(errorMessage(error, t, "shipments.shipFailed"));
    }
  };

  return (
    <DialogForm onSubmit={submit} saving={ship.isPending} submitLabel={t("shipments.ship")}>
      <p className="text-sm text-muted-foreground">{t("shipments.shipBody")}</p>
      <Field label={t("shipments.shippedOn")}>
        <Input
          type="date"
          required
          value={shippedOn}
          onChange={(event) => setShippedOn(event.target.value)}
        />
      </Field>
    </DialogForm>
  );
}

function ReturnForm({
  shipment,
  line,
  onSaved,
}: {
  shipment: OrderShipmentRead;
  line: OrderShipmentLineRead;
  onSaved: (saved: OrderRead) => void;
}) {
  const { t } = useTranslation("orders");
  const { item } = useMovementLabels();
  const [quantity, setQuantity] = useState("");
  const [datedOn, setDatedOn] = useState(today());
  const [damaged, setDamaged] = useState(false);
  const record = useCreateShipmentReturn();

  const submit = async () => {
    try {
      onSaved(
        await record.mutateAsync({
          shipmentId: shipment.id,
          lineId: line.id,
          data: { quantity, dated_on: datedOn, damaged },
        })
      );
    } catch (error) {
      toast.error(errorMessage(error, t, "shipments.returnFailed"));
    }
  };

  return (
    <DialogForm
      onSubmit={submit}
      saving={record.isPending}
      submitLabel={t("shipments.recordReturn")}
    >
      <p className="text-sm text-muted-foreground">{item(line.item)}</p>
      <div className="grid grid-cols-pair gap-4">
        <Field label={t("hoard:common.quantity")}>
          <Input
            required
            inputMode="decimal"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
          />
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
      <div className="flex items-center gap-2">
        <Checkbox
          id="return-damaged"
          checked={damaged}
          onCheckedChange={(checked) => setDamaged(checked === true)}
        />
        <Label htmlFor="return-damaged">{t("shipments.damaged")}</Label>
      </div>
      <p className="text-sm text-muted-foreground">
        {damaged ? t("shipments.damagedHint") : t("shipments.restockHint")}
      </p>
    </DialogForm>
  );
}
