import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Ban, Pencil, Plus, RotateCcw, Trash2, Truck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { getListAdjustmentsQueryKey } from "@/api/generated/adjustments/adjustments";
import type {
  OrderLineRead,
  OrderLot,
  OrderRead,
  OrderShipmentRead,
} from "@/api/generated/hoard.schemas";
import {
  getListOrdersQueryKey,
  getReadOrderQueryKey,
  useCancelOrder,
  useDeleteOrder,
  useDeleteOrderLine,
  useReadOrder,
  useReopenOrder,
} from "@/api/generated/orders/orders";
import {
  useDeleteShipment,
  useDeleteShipmentLine,
  useDeleteShipmentReturn,
  useUnshipShipment,
  useUpdateShipmentLine,
} from "@/api/generated/shipments/shipments";
import { toast } from "@/components/Toaster";
import { useMovementLabels } from "@/features/adjustments/labels";
import { useContactNames } from "@/features/contacts/ContactPicker";
import { HoldFromOrder } from "@/features/holds/HoldFromOrder";
import { OnHoldBadge } from "@/features/items/lots";
import { type LineAction, LineDialog } from "@/features/orders/LineDialog";
import { OrderDialog } from "@/features/orders/OrderDialog";
import { OrderBadges, useOrderFor } from "@/features/orders/OrdersPage";
import { leftToShip, type ShipmentAction, ShipmentDialog } from "@/features/orders/ShipmentDialogs";
import { errorMessage } from "@/lib/errors";
import { useMemberNames } from "@/lib/members";
import { useMoney } from "@/lib/money";
import { useSession } from "@/session";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/ui/card";
import { ConfirmDialog } from "@/ui/confirm-dialog";
import { DropdownMenuItem } from "@/ui/dropdown-menu";
import { RowActionsMenu } from "@/ui/row-actions-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** The lot choice that leaves Hoard to assume one when the shipment ships. */
const ASSUMED = "assumed";

/** One order, under Orders: its customer and lines, its shipments with the
 *  lots they draw and what came back, cancelling and reopening it. */
export function OrderPage({ orderId }: { orderId: number }) {
  const { t } = useTranslation("orders");
  const order = useReadOrder(orderId);
  if (order.isError)
    return (
      <StatusMessage
        description={errorMessage(order.error, t, "order.loadFailed")}
        backTo="/orders"
        backLabel={t("order.back")}
      />
    );
  if (!order.data) return <p className="text-muted-foreground">{t("hoard:common.loading")}</p>;
  return <Order order={order.data} />;
}

/** Keeps the order read as `next`, and refreshes what it changes elsewhere. */
function useSaved() {
  const queries = useQueryClient();
  return (next: OrderRead) => {
    queries.setQueryData(getReadOrderQueryKey(next.id), next);
    void queries.invalidateQueries({ queryKey: getListOrdersQueryKey() });
    void queries.invalidateQueries({ queryKey: getListAdjustmentsQueryKey() });
  };
}

/** Sends a change to the order, and keeps what it answers. */
function useChange() {
  const { t } = useTranslation("orders");
  const saved = useSaved();
  return async (making: Promise<OrderRead>) => {
    try {
      saved(await making);
    } catch (error) {
      toast.error(errorMessage(error, t, "order.saveFailed"));
    }
  };
}

function Order({ order }: { order: OrderRead }) {
  const { t } = useTranslation("orders");
  const queries = useQueryClient();
  const navigate = useNavigate();
  const { read_only } = useSession();
  const money = useMoney();
  const { date, location, item, quantity } = useMovementLabels();
  const orderFor = useOrderFor();
  const memberName = useMemberNames([order.created_by]);
  const saved = useSaved();
  const change = useChange();
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [lineAction, setLineAction] = useState<LineAction | null>(null);
  const [shipmentAction, setShipmentAction] = useState<ShipmentAction | null>(null);
  const remove = useDeleteOrder();
  const removeLine = useDeleteOrderLine();
  const cancel = useCancelOrder();
  const reopen = useReopenOrder();
  const cancelled = order.status === "cancelled";
  const byHand = order.store_id === null;
  const editsLines = byHand && !cancelled && !read_only;

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <h2 className="flex flex-wrap items-center gap-2 text-xl font-semibold tracking-tight">
            {t("order.title", { code: order.code })}
            <OrderBadges order={order} />
            {order.lines.some((line) => Number(line.short) > 0) && (
              <Badge variant="warning">{t("order.short")}</Badge>
            )}
          </h2>
          <p className="text-sm text-muted-foreground">
            {[
              date(order.ordered_on),
              orderFor(order),
              location(order.location_id),
              order.due_on ? t("order.due", { date: date(order.due_on) }) : "",
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {order.delivery_address && (
            <p className="text-sm whitespace-pre-line">{order.delivery_address}</p>
          )}
          <p className="text-xs text-muted-foreground">
            {t("hoard:common.createdBy", { name: memberName(order.created_by) })}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <HoldFromOrder order={order} />
          <Button variant="outline" onClick={() => setEditing(true)} disabled={read_only}>
            <Pencil aria-hidden />
            {t("hoard:common.edit")}
          </Button>
          {cancelled ? (
            <Button
              variant="outline"
              disabled={read_only || reopen.isPending}
              onClick={() => void change(reopen.mutateAsync({ orderId: order.id }))}
            >
              <RotateCcw aria-hidden />
              {t("order.reopen")}
            </Button>
          ) : (
            <Button
              variant="outline"
              disabled={read_only || cancel.isPending}
              onClick={() => void change(cancel.mutateAsync({ orderId: order.id }))}
            >
              <Ban aria-hidden />
              {t("order.cancel")}
            </Button>
          )}
          {byHand && (
            <Button variant="outline" onClick={() => setDeleting(true)} disabled={read_only}>
              <Trash2 aria-hidden />
              {t("hoard:common.delete")}
            </Button>
          )}
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("lines.item")}</TableHead>
            <TableHead className="text-right">{t("hoard:common.quantity")}</TableHead>
            <TableHead className="text-right">{t("lines.price")}</TableHead>
            <TableHead className="text-right">{t("lines.shipped")}</TableHead>
            <TableHead>
              <span className="sr-only">{t("lines.actions")}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {order.lines.map((line) => (
            <TableRow key={line.id}>
              <TableCell className={line.bundle_line_id ? "pl-8" : undefined}>
                {line.bundle_line_id ? t("lines.part", { item: item(line.item) }) : item(line.item)}
              </TableCell>
              <TableCell className="text-right">{quantity(line.quantity)}</TableCell>
              <TableCell className="text-right">
                {line.bundle_line_id ? "" : money(line.price, { exact: true })}
              </TableCell>
              <TableCell className="text-right">
                <Shipped
                  line={line}
                  bundle={order.lines.some((one) => one.bundle_line_id === line.id)}
                />
              </TableCell>
              <TableCell className="text-right">
                {editsLines && !line.bundle_line_id && (
                  <RowActionsMenu subject={item(line.item)}>
                    <DropdownMenuItem onSelect={() => setLineAction({ kind: "edit", line })}>
                      {t("hoard:common.edit")}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() =>
                        void change(removeLine.mutateAsync({ orderId: order.id, lineId: line.id }))
                      }
                    >
                      {t("hoard:common.remove")}
                    </DropdownMenuItem>
                  </RowActionsMenu>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {order.lines.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("lines.empty")}</p>
      )}
      {editsLines && (
        <div>
          <Button variant="outline" onClick={() => setLineAction({ kind: "add" })}>
            <Plus aria-hidden />
            {t("lines.add")}
          </Button>
        </div>
      )}

      <dl className="grid grid-cols-fill-40/4 gap-4 text-sm">
        {(["discount", "shipping", "tax", "total"] as const).map((name) => (
          <div key={name}>
            <dt className="text-muted-foreground">
              {name === "total" ? t("hoard:common.total") : t(`order.${name}`)}
            </dt>
            <dd className="font-medium">{money(order[name])}</dd>
          </div>
        ))}
      </dl>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-lg font-semibold tracking-tight">{t("shipments.title")}</h3>
        {!cancelled && leftToShip(order).length > 0 && (
          <Button
            variant="outline"
            disabled={read_only}
            onClick={() => setShipmentAction({ kind: "add" })}
          >
            <Truck aria-hidden />
            {t("shipments.add")}
          </Button>
        )}
      </div>
      {order.shipments.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("shipments.empty")}</p>
      )}
      {order.shipments.map((shipment, index) => (
        <ShipmentCard
          key={shipment.id}
          shipment={shipment}
          number={index + 1}
          cancelled={cancelled}
          onAction={setShipmentAction}
        />
      ))}

      <OrderDialog open={editing} onOpenChange={setEditing} order={order} />
      <LineDialog
        order={order}
        action={lineAction}
        onClose={() => setLineAction(null)}
        onSaved={saved}
      />
      <ShipmentDialog
        order={order}
        action={shipmentAction}
        onClose={() => setShipmentAction(null)}
        onSaved={saved}
      />
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={t("order.deleteTitle", { code: order.code })}
        description={t("order.deleteBody")}
        confirmLabel={t("hoard:common.delete")}
        destructive
        isLoading={remove.isPending}
        onConfirm={async () => {
          try {
            await remove.mutateAsync({ orderId: order.id });
            void queries.invalidateQueries({ queryKey: getListOrdersQueryKey() });
            void navigate({ to: "/orders" });
          } catch (error) {
            setDeleting(false);
            toast.error(errorMessage(error, t, "order.deleteFailed"));
          }
        }}
      />
    </section>
  );
}

/** How much of a line has shipped, and how short it is; nothing for a
 *  bundle, whose parts ship. */
function Shipped({ line, bundle }: { line: OrderLineRead; bundle: boolean }) {
  const { t } = useTranslation("orders");
  const { quantity } = useMovementLabels();
  if (bundle) return null;
  return (
    <span className="inline-flex flex-wrap items-center justify-end gap-2">
      {Number(line.short) > 0 && (
        <Badge variant="warning" title={t("lines.shortHint")}>
          {t("lines.short", { quantity: quantity(line.short) })}
        </Badge>
      )}
      {t("lines.shippedOf", { shipped: quantity(line.shipped), quantity: quantity(line.quantity) })}
    </span>
  );
}

function ShipmentCard({
  shipment,
  number,
  cancelled,
  onAction,
}: {
  shipment: OrderShipmentRead;
  number: number;
  cancelled: boolean;
  onAction: (action: ShipmentAction) => void;
}) {
  const { t } = useTranslation("orders");
  const { read_only } = useSession();
  const { date, location, item, quantity } = useMovementLabels();
  const contactName = useContactNames();
  const carrier = contactName(shipment.carrier_contact_id);
  const change = useChange();
  const unship = useUnshipShipment();
  const remove = useDeleteShipment();
  const removeLine = useDeleteShipmentLine();
  const updateLine = useUpdateShipmentLine();
  const removeReturn = useDeleteShipmentReturn();
  const pending = shipment.status === "pending";
  const shipped = shipment.status === "shipped";
  const lotName = (lot: OrderLot) =>
    lot.on_hold
      ? t("shipments.lotOnHold", { lot: lot.number })
      : lot.expires_on
        ? t("shipments.lotExpires", {
            number: lot.number,
            date: date(lot.expires_on),
            balance: quantity(lot.balance),
          })
        : t("shipments.lotOption", { number: lot.number, balance: quantity(lot.balance) });

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            {t("shipments.cardTitle", { number })}
            <Badge variant={shipped ? "outline" : "secondary"}>
              {t(`shipments.status.${shipment.status}`)}
            </Badge>
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            {[
              shipment.shipped_on ? date(shipment.shipped_on) : "",
              location(shipment.location_id),
              carrier ? t("shipments.carrierIs", { carrier }) : "",
              shipment.tracking ? t("shipments.trackingIs", { tracking: shipment.tracking }) : "",
              shipment.reference ?? "",
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        {!read_only && (
          <div className="flex flex-wrap gap-2">
            {pending && !cancelled && (
              <Button size="sm" onClick={() => onAction({ kind: "ship", shipment })}>
                <Truck aria-hidden />
                {t("shipments.ship")}
              </Button>
            )}
            <RowActionsMenu subject={t("shipments.cardTitle", { number })}>
              <DropdownMenuItem onSelect={() => onAction({ kind: "edit", shipment })}>
                {t("hoard:common.edit")}
              </DropdownMenuItem>
              {shipped && (
                <DropdownMenuItem
                  onSelect={() => void change(unship.mutateAsync({ shipmentId: shipment.id }))}
                >
                  {t("shipments.unship")}
                </DropdownMenuItem>
              )}
              {!shipped && (
                <DropdownMenuItem
                  onSelect={() => void change(remove.mutateAsync({ shipmentId: shipment.id }))}
                >
                  {t("hoard:common.delete")}
                </DropdownMenuItem>
              )}
            </RowActionsMenu>
          </div>
        )}
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("lines.item")}</TableHead>
              <TableHead className="text-right">{t("hoard:common.quantity")}</TableHead>
              <TableHead>{t("shipments.lot")}</TableHead>
              <TableHead>{t("shipments.returned")}</TableHead>
              <TableHead>
                <span className="sr-only">{t("lines.actions")}</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {shipment.lines.map((line) => (
              <TableRow key={line.id}>
                <TableCell>{item(line.item)}</TableCell>
                <TableCell className="text-right">{quantity(line.quantity)}</TableCell>
                <TableCell>
                  {pending && !read_only ? (
                    <Select
                      value={line.lot ? String(line.lot.id) : ASSUMED}
                      onValueChange={(next) =>
                        void change(
                          updateLine.mutateAsync({
                            shipmentId: shipment.id,
                            lineId: line.id,
                            data: { lot_id: next === ASSUMED ? null : Number(next) },
                          })
                        )
                      }
                    >
                      <SelectTrigger aria-label={t("shipments.lot")} className="min-w-48">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={ASSUMED}>{t("shipments.assumeLot")}</SelectItem>
                        {[
                          ...line.lots,
                          ...(line.lot && !line.lots.some((lot) => lot.id === line.lot?.id)
                            ? [line.lot]
                            : []),
                        ].map((lot) => (
                          <SelectItem key={lot.id} value={String(lot.id)}>
                            {lotName(lot)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : line.lot ? (
                    <span className="inline-flex flex-wrap items-center gap-2">
                      {line.lot.number}
                      {line.assumed && <Badge variant="secondary">{t("shipments.assumed")}</Badge>}
                      {line.lot.on_hold && <OnHoldBadge />}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">{t("shipments.noLot")}</span>
                  )}
                </TableCell>
                <TableCell>
                  <ul className="space-y-1">
                    {line.returns.map((back) => (
                      <li key={back.id} className="flex flex-wrap items-center gap-2 text-sm">
                        {t("shipments.returnedOn", {
                          quantity: quantity(back.quantity),
                          date: date(back.dated_on),
                        })}
                        {back.damaged && (
                          <Badge variant="warning">{t("shipments.damagedBadge")}</Badge>
                        )}
                        {!read_only && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() =>
                              void change(
                                removeReturn.mutateAsync({
                                  shipmentId: shipment.id,
                                  returnId: back.id,
                                })
                              )
                            }
                          >
                            {t("shipments.undoReturn")}
                          </Button>
                        )}
                      </li>
                    ))}
                  </ul>
                </TableCell>
                <TableCell className="text-right">
                  {!read_only && shipped && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => onAction({ kind: "return", shipment, line })}
                    >
                      {t("shipments.return")}
                    </Button>
                  )}
                  {!read_only && pending && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        void change(
                          removeLine.mutateAsync({ shipmentId: shipment.id, lineId: line.id })
                        )
                      }
                    >
                      {t("hoard:common.remove")}
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
