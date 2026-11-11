import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowUpDown, Plus } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  getListAdjustmentsQueryKey,
  useDeleteAdjustment,
  useListAdjustments,
} from "@/api/generated/adjustments/adjustments";
import { AdjustmentKind, type AdjustmentRead } from "@/api/generated/hoard.schemas";
import { toast } from "@/components/Toaster";
import { AdjustmentDialog } from "@/features/adjustments/AdjustmentDialog";
import { ItemField } from "@/features/adjustments/fields";
import { useMovementLabels } from "@/features/adjustments/labels";
import { errorMessage } from "@/lib/errors";
import { useMemberNames } from "@/lib/members";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { useSession } from "@/session";
import { Button } from "@/ui/button";
import { DropdownMenuItem } from "@/ui/dropdown-menu";
import { Field } from "@/ui/field";
import { PaginationBar } from "@/ui/pagination-bar";
import { RowActionsMenu } from "@/ui/row-actions-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** How many adjustments a page lists. */
const PAGE_SIZE = 50;
/** The kind filter's value for every kind. */
const ALL_KINDS = "all";

/** Every change to stock, under Stock: of one item or one kind, and changes
 *  made by hand. */
export function AdjustmentsPage() {
  const { t } = useTranslation("movements");
  const { read_only } = useSession();
  const [page, setPage] = useState(1);
  const [kind, setKind] = useState<AdjustmentKind | null>(null);
  const [itemId, setItemId] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<AdjustmentRead | null>(null);
  const adjustments = useListAdjustments({
    offset: (page - 1) * PAGE_SIZE,
    limit: PAGE_SIZE,
    ...(kind ? { kind } : {}),
    ...(itemId === null ? {} : { item_id: itemId }),
  });
  const filtered = kind !== null || itemId !== null;

  let body: ReactNode;
  if (adjustments.isError) body = <StatusMessage onRetry={() => void adjustments.refetch()} />;
  else if (!adjustments.data)
    body = <p className="text-muted-foreground">{t("hoard:common.loading")}</p>;
  else if (adjustments.data.total === 0)
    body = filtered ? (
      <p className="text-sm text-muted-foreground">{t("adjustments.noneMatch")}</p>
    ) : (
      <StatusMessage
        icon={<ArrowUpDown />}
        title={t("adjustments.emptyTitle")}
        description={t("adjustments.emptyBody")}
      />
    );
  else
    body = (
      <>
        <AdjustmentsTable adjustments={adjustments.data.items} onEdit={setEditing} />
        {adjustments.data.total > PAGE_SIZE && (
          <PaginationBar
            page={page}
            pageSize={PAGE_SIZE}
            totalCount={adjustments.data.total}
            hasNext={page * PAGE_SIZE < adjustments.data.total}
            onPageChange={setPage}
          />
        )}
      </>
    );

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-4">
        <Field label={t("adjustments.kind")} className="min-w-40">
          <Select
            value={kind ?? ALL_KINDS}
            onValueChange={(next) => {
              setKind(next === ALL_KINDS ? null : (next as AdjustmentKind));
              setPage(1);
            }}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_KINDS}>{t("adjustments.allKinds")}</SelectItem>
              {Object.values(AdjustmentKind).map((each) => (
                <SelectItem key={each} value={each}>
                  {t(`adjustments.kinds.${each}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <ItemField
          label={t("adjustments.item")}
          value={itemId}
          onChange={(picked) => {
            setItemId(picked);
            setPage(1);
          }}
          clearLabel={t("adjustments.allItems")}
        />
        <Button className="ml-auto" onClick={() => setAdding(true)} disabled={read_only}>
          <Plus aria-hidden />
          {t("adjustments.add")}
        </Button>
      </div>
      {body}
      <AdjustmentDialog open={adding} onOpenChange={setAdding} />
      <AdjustmentDialog
        open={editing !== null}
        onOpenChange={(open) => !open && setEditing(null)}
        adjustment={editing ?? undefined}
      />
    </section>
  );
}

function AdjustmentsTable({
  adjustments,
  onEdit,
}: {
  adjustments: AdjustmentRead[];
  onEdit: (adjustment: AdjustmentRead) => void;
}) {
  const { t } = useTranslation("movements");
  const queries = useQueryClient();
  const { read_only } = useSession();
  const seesCosts = useSeesCosts();
  const money = useMoney();
  const { date, item, location, reason, quantity } = useMovementLabels();
  const memberName = useMemberNames(adjustments.map((adjustment) => adjustment.created_by));
  const remove = useDeleteAdjustment();

  const cause = (adjustment: AdjustmentRead) => {
    if (adjustment.purchase_id !== null)
      return (
        <Link
          to="/stock/purchases/$purchaseId"
          params={{ purchaseId: String(adjustment.purchase_id) }}
          className="underline-offset-4 hover:underline"
        >
          {t("adjustments.kinds.purchase")}
        </Link>
      );
    if (adjustment.stocktake_id !== null)
      return (
        <Link
          to="/stock/stocktakes/$stocktakeId"
          params={{ stocktakeId: String(adjustment.stocktake_id) }}
          className="underline-offset-4 hover:underline"
        >
          {t("adjustments.kinds.stocktake")}
        </Link>
      );
    const why = reason(adjustment.reason_id);
    return why || t(`adjustments.kinds.${adjustment.kind}`);
  };

  const deleteAdjustment = async (adjustment: AdjustmentRead) => {
    try {
      await remove.mutateAsync({ adjustmentId: adjustment.id });
      void queries.invalidateQueries({ queryKey: getListAdjustmentsQueryKey() });
    } catch (error) {
      toast.error(errorMessage(error, t, "adjustments.saveFailed"));
    }
  };

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("hoard:common.date")}</TableHead>
          <TableHead>{t("adjustments.item")}</TableHead>
          <TableHead className="text-right">{t("adjustments.change")}</TableHead>
          {seesCosts && <TableHead className="text-right">{t("hoard:common.unitCost")}</TableHead>}
          <TableHead>{t("adjustments.why")}</TableHead>
          <TableHead>{t("hoard:common.location")}</TableHead>
          <TableHead>{t("adjustments.by")}</TableHead>
          <TableHead>
            <span className="sr-only">{t("adjustments.actions")}</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {adjustments.map((adjustment) => {
          const editable = adjustment.kind === "manual" || adjustment.kind === "starting";
          return (
            <TableRow key={adjustment.id}>
              <TableCell>{date(adjustment.dated_on)}</TableCell>
              <TableCell>
                {item(adjustment.item)}
                {adjustment.notes && (
                  <p className="text-xs text-muted-foreground">{adjustment.notes}</p>
                )}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {adjustment.change.startsWith("-")
                  ? quantity(adjustment.change)
                  : t("adjustments.added", { quantity: quantity(adjustment.change) })}
              </TableCell>
              {seesCosts && (
                <TableCell className="text-right">
                  {money(adjustment.unit_cost ?? "0", { exact: true })}
                </TableCell>
              )}
              <TableCell>{cause(adjustment)}</TableCell>
              <TableCell>{location(adjustment.location_id)}</TableCell>
              <TableCell>{memberName(adjustment.created_by)}</TableCell>
              <TableCell className="text-right">
                {editable && !read_only && (
                  <RowActionsMenu subject={item(adjustment.item)}>
                    <DropdownMenuItem onSelect={() => onEdit(adjustment)}>
                      {t("hoard:common.edit")}
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => void deleteAdjustment(adjustment)}>
                      {t("hoard:common.delete")}
                    </DropdownMenuItem>
                  </RowActionsMenu>
                )}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
