import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { PackageCheck, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { getListAdjustmentsQueryKey } from "@/api/generated/adjustments/adjustments";
import type { PurchaseRead } from "@/api/generated/hoard.schemas";
import {
  getListPurchasesQueryKey,
  getReadPurchaseQueryKey,
  useDeletePurchase,
  useDeletePurchaseLine,
  useReadPurchase,
  useUnreceivePurchaseLine,
} from "@/api/generated/purchases/purchases";
import { toast } from "@/components/Toaster";
import { useMovementLabels } from "@/features/adjustments/labels";
import { useSupplierNames } from "@/features/contacts/SupplierPicker";
import { type LineAction, LineDialog } from "@/features/purchases/LineDialogs";
import { PurchaseDialog } from "@/features/purchases/PurchaseDialog";
import { errorMessage } from "@/lib/errors";
import { useMemberNames } from "@/lib/members";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { useSession } from "@/session";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";
import { ConfirmDialog } from "@/ui/confirm-dialog";
import { DropdownMenuItem } from "@/ui/dropdown-menu";
import { RowActionsMenu } from "@/ui/row-actions-menu";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** One purchase, under Stock: its supplier and lines, receiving them, and its
 *  costs for a member who sees them. */
export function PurchasePage({ purchaseId }: { purchaseId: number }) {
  const { t } = useTranslation("movements");
  const purchase = useReadPurchase(purchaseId);
  if (purchase.isError)
    return (
      <StatusMessage
        description={errorMessage(purchase.error, t, "purchases.loadFailed")}
        backTo="/stock/purchases"
        backLabel={t("purchases.back")}
      />
    );
  if (!purchase.data) return <p className="text-muted-foreground">{t("hoard:common.loading")}</p>;
  return <Purchase purchase={purchase.data} />;
}

function Purchase({ purchase }: { purchase: PurchaseRead }) {
  const { t } = useTranslation("movements");
  const queries = useQueryClient();
  const navigate = useNavigate();
  const { read_only } = useSession();
  const seesCosts = useSeesCosts();
  const money = useMoney();
  const { date, location, item, quantity } = useMovementLabels();
  const supplier = useSupplierNames();
  const memberName = useMemberNames([purchase.created_by]);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [lineAction, setLineAction] = useState<LineAction | null>(null);
  const remove = useDeletePurchase();
  const removeLine = useDeletePurchaseLine();
  const unreceive = useUnreceivePurchaseLine();

  const saved = (next: PurchaseRead) => {
    queries.setQueryData(getReadPurchaseQueryKey(next.id), next);
    void queries.invalidateQueries({ queryKey: getListPurchasesQueryKey() });
    void queries.invalidateQueries({ queryKey: getListAdjustmentsQueryKey() });
  };
  const change = async (making: Promise<PurchaseRead>) => {
    try {
      saved(await making);
    } catch (error) {
      toast.error(errorMessage(error, t, "purchases.saveFailed"));
    }
  };

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <h2 className="text-xl font-semibold tracking-tight">
            {t("purchases.title", { code: purchase.code })}
            {purchase.paid && (
              <Badge variant="secondary" className="ml-2 align-middle">
                {t("purchases.paid")}
              </Badge>
            )}
          </h2>
          <p className="text-muted-foreground text-sm">
            {[date(purchase.dated_on), supplier(purchase.supplier_contact_id), location(purchase.location_id)]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <p className="text-muted-foreground text-xs">
            {t("hoard:common.createdBy", { name: memberName(purchase.created_by) })}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => setEditing(true)} disabled={read_only}>
            <Pencil aria-hidden />
            {t("hoard:common.edit")}
          </Button>
          <Button variant="outline" onClick={() => setDeleting(true)} disabled={read_only}>
            <Trash2 aria-hidden />
            {t("hoard:common.delete")}
          </Button>
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("purchases.lines.material")}</TableHead>
            <TableHead className="text-right">{t("hoard:common.quantity")}</TableHead>
            {seesCosts && (
              <>
                <TableHead className="text-right">{t("hoard:common.unitCost")}</TableHead>
                <TableHead className="text-right">{t("purchases.lines.landedCost")}</TableHead>
              </>
            )}
            <TableHead>{t("purchases.lines.lot")}</TableHead>
            <TableHead>
              <span className="sr-only">{t("purchases.lines.actions")}</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {purchase.lines.map((line) => (
            <TableRow key={line.id}>
              <TableCell>{item(line.material)}</TableCell>
              <TableCell className="text-right">
                {line.purchase_quantity === line.tracking_quantity
                  ? quantity(line.purchase_quantity)
                  : t("purchases.lines.converted", {
                      bought: quantity(line.purchase_quantity),
                      tracked: quantity(line.tracking_quantity),
                    })}
              </TableCell>
              {seesCosts && (
                <>
                  <TableCell className="text-right">
                    {money(line.unit_cost ?? "0", { exact: true })}
                  </TableCell>
                  <TableCell className="text-right">
                    {money(line.landed_cost ?? "0", { exact: true })}
                  </TableCell>
                </>
              )}
              <TableCell>
                {line.received ? (
                  (line.lot?.number ?? t("purchases.lines.received"))
                ) : (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={read_only}
                    onClick={() => setLineAction({ kind: "receive", line })}
                  >
                    <PackageCheck aria-hidden />
                    {t("purchases.lines.receive")}
                  </Button>
                )}
              </TableCell>
              <TableCell className="text-right">
                {!read_only && (
                  <RowActionsMenu subject={item(line.material)}>
                    <DropdownMenuItem onSelect={() => setLineAction({ kind: "edit", line })}>
                      {t("hoard:common.edit")}
                    </DropdownMenuItem>
                    {line.received && (
                      <DropdownMenuItem
                        onSelect={() =>
                          void change(
                            unreceive.mutateAsync({ purchaseId: purchase.id, lineId: line.id })
                          )
                        }
                      >
                        {t("purchases.lines.unreceive")}
                      </DropdownMenuItem>
                    )}
                    <DropdownMenuItem
                      onSelect={() =>
                        void change(
                          removeLine.mutateAsync({ purchaseId: purchase.id, lineId: line.id })
                        )
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
      {purchase.lines.length === 0 && (
        <p className="text-muted-foreground text-sm">{t("purchases.lines.empty")}</p>
      )}
      <div>
        <Button variant="outline" onClick={() => setLineAction({ kind: "add" })} disabled={read_only}>
          <Plus aria-hidden />
          {t("purchases.lines.add")}
        </Button>
      </div>

      {seesCosts && (
        <dl className="grid-cols-fill-40/4 grid gap-4 text-sm">
          {(["discount", "shipping", "tax", "total"] as const).map((name) => (
            <div key={name}>
              <dt className="text-muted-foreground">
                {name === "total" ? t("hoard:common.total") : t(`purchases.${name}`)}
              </dt>
              <dd className="font-medium">{money(purchase[name] ?? "0")}</dd>
            </div>
          ))}
        </dl>
      )}

      <PurchaseDialog open={editing} onOpenChange={setEditing} purchase={purchase} />
      <LineDialog
        purchase={purchase}
        action={lineAction}
        onClose={() => setLineAction(null)}
        onSaved={saved}
      />
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={t("purchases.deleteTitle", { code: purchase.code })}
        description={t("purchases.deleteBody")}
        confirmLabel={t("hoard:common.delete")}
        destructive
        isLoading={remove.isPending}
        onConfirm={async () => {
          try {
            await remove.mutateAsync({ purchaseId: purchase.id });
            void queries.invalidateQueries({ queryKey: getListPurchasesQueryKey() });
            void queries.invalidateQueries({ queryKey: getListAdjustmentsQueryKey() });
            void navigate({ to: "/stock/purchases" });
          } catch (error) {
            setDeleting(false);
            toast.error(errorMessage(error, t, "purchases.deleteFailed"));
          }
        }}
      />
    </section>
  );
}
