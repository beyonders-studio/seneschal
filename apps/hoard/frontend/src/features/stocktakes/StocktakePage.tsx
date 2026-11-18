import { ScanButton, useScanTarget } from "@seneschal/app/scan";
import { useQueryClient } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { getListAdjustmentsQueryKey } from "@/api/generated/adjustments/adjustments";
import type { StocktakeRead, StocktakeRowRead } from "@/api/generated/hoard.schemas";
import { scanCode } from "@/api/generated/scans/scans";
import {
  addStocktakeLot,
  finishStocktake,
  getListStocktakesQueryKey,
  getReadStocktakeQueryKey,
  useConfirmStocktakeRow,
  useReadStocktake,
} from "@/api/generated/stocktakes/stocktakes";
import { toast } from "@/components/Toaster";
import { useMovementLabels } from "@/features/adjustments/labels";
import { useStocktakeProgress } from "@/features/stocktakes/StocktakesPage";
import { errorMessage } from "@/lib/errors";
import { useMemberNames } from "@/lib/members";
import { useSession } from "@/session";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";
import { ConfirmDialog } from "@/ui/confirm-dialog";
import { Input } from "@/ui/input";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** One stocktake, under Stock: its rows, each confirmed as it is counted, and
 *  its accuracy. */
export function StocktakePage({ stocktakeId }: { stocktakeId: number }) {
  const { t } = useTranslation("movements");
  const stocktake = useReadStocktake(stocktakeId);
  if (stocktake.isError)
    return (
      <StatusMessage
        description={errorMessage(stocktake.error, t, "stocktakes.loadFailed")}
        backTo="/stock/stocktakes"
        backLabel={t("stocktakes.back")}
      />
    );
  if (!stocktake.data) return <p className="text-muted-foreground">{t("hoard:common.loading")}</p>;
  return <Stocktake stocktake={stocktake.data} />;
}

function Stocktake({ stocktake }: { stocktake: StocktakeRead }) {
  const { t } = useTranslation("movements");
  const queries = useQueryClient();
  const { read_only } = useSession();
  const { date, location, category, item } = useMovementLabels();
  const progress = useStocktakeProgress();
  const memberName = useMemberNames([stocktake.created_by]);
  // The row a scan found, whose count field takes the focus.
  const [focused, setFocused] = useState<number | null>(null);
  const [finishing, setFinishing] = useState(false);

  const saved = (next: StocktakeRead) => {
    queries.setQueryData(getReadStocktakeQueryKey(stocktake.id), next);
    void queries.invalidateQueries({ queryKey: getListStocktakesQueryKey() });
    void queries.invalidateQueries({ queryKey: getListAdjustmentsQueryKey() });
  };

  /** Find the row a scanned label counts, adding a lot found while counting. */
  const scanRow = async (text: string) => {
    try {
      const read = await scanCode({ text, location_id: stocktake.location_id });
      const [lot] = read.lots;
      let current = stocktake;
      if (lot && stocktake.lot_count) {
        if (!stocktake.rows.some((row) => row.lot?.id === lot.id)) {
          current = await addStocktakeLot(stocktake.id, { lot_id: lot.id });
          saved(current);
        }
        const row = current.rows.find((each) => each.lot?.id === lot.id);
        if (row) return setFocused(row.id);
      }
      const itemIds = new Set([
        ...read.items.map((each) => each.id),
        ...read.lots.map((each) => each.item_id),
      ]);
      const row = current.rows.find((each) => itemIds.has(each.item.id));
      if (row) return setFocused(row.id);
      toast.error(t("stocktakes.scanNotCounted"));
    } catch (error) {
      toast.error(errorMessage(error, t, "stocktakes.scanFailed"));
    }
  };

  useScanTarget(read_only ? null : (text) => void scanRow(text));

  const finish = async () => {
    try {
      saved(await finishStocktake(stocktake.id));
    } catch (error) {
      toast.error(errorMessage(error, t, "stocktakes.finishFailed"));
    } finally {
      setFinishing(false);
    }
  };
  return (
    <section className="flex flex-col gap-4">
      <div className="space-y-1">
        <h2 className="text-xl font-semibold tracking-tight">
          {t("stocktakes.title", { date: date(stocktake.dated_on) })}
          {stocktake.finished && (
            <Badge variant="secondary" className="ml-2 align-middle">
              {t("stocktakes.finished")}
            </Badge>
          )}
        </h2>
        <p className="text-sm text-muted-foreground">
          {[
            t(`stocktakes.kinds.${stocktake.kind}`),
            category(stocktake.category_id),
            location(stocktake.location_id),
            progress(stocktake),
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
        {stocktake.notes && <p className="text-sm">{stocktake.notes}</p>}
        {stocktake.lot_count && (
          <p className="text-sm text-muted-foreground">
            {t(`stocktakes.lots.hints.${stocktake.lot_count}`)}
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          {t("hoard:common.createdBy", { name: memberName(stocktake.created_by) })}
        </p>
      </div>
      {!read_only && (
        <div className="flex flex-wrap justify-end gap-2">
          <ScanButton
            size="sm"
            label={t("stocktakes.scan")}
            onScan={(text) => void scanRow(text)}
          />
          {stocktake.lot_count === "full" && !stocktake.finished && (
            <Button size="sm" variant="outline" onClick={() => setFinishing(true)}>
              {t("stocktakes.finish")}
            </Button>
          )}
        </div>
      )}
      <ConfirmDialog
        open={finishing}
        onOpenChange={setFinishing}
        title={t("stocktakes.finishTitle")}
        description={t("stocktakes.finishBody")}
        confirmLabel={t("stocktakes.finish")}
        onConfirm={() => void finish()}
      />
      {stocktake.rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("stocktakes.noRows")}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("stocktakes.item")}</TableHead>
              <TableHead className="text-right">{t("stocktakes.expected")}</TableHead>
              <TableHead>{t("stocktakes.countedLabel")}</TableHead>
              <TableHead className="text-right">{t("stocktakes.variance")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {stocktake.rows.map((row) => (
              <StocktakeRowLine
                key={row.id}
                stocktake={stocktake}
                row={row}
                focused={focused === row.id}
                name={
                  row.lot
                    ? t("stocktakes.lotOf", { item: item(row.item), lot: row.lot.number })
                    : stocktake.lot_count
                      ? t("stocktakes.noLotOf", { item: item(row.item) })
                      : item(row.item)
                }
              />
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}

function StocktakeRowLine({
  stocktake,
  row,
  name,
  focused,
}: {
  stocktake: StocktakeRead;
  row: StocktakeRowRead;
  /** The item, with the lot it counts. */
  name: string;
  /** A scan found it: its count takes the focus. */
  focused: boolean;
}) {
  const { t } = useTranslation("movements");
  const queries = useQueryClient();
  const { read_only } = useSession();
  const { quantity } = useMovementLabels();
  const [counted, setCounted] = useState(row.counted ?? "");
  const confirm = useConfirmStocktakeRow();
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!focused) return;
    field.current?.scrollIntoView?.({ block: "center" });
    field.current?.focus();
  }, [focused]);
  const variance =
    row.counted !== null && row.expected !== null
      ? Number(row.counted) - Number(row.expected)
      : null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const saved = await confirm.mutateAsync({
        stocktakeId: stocktake.id,
        rowId: row.id,
        data: { counted },
      });
      queries.setQueryData(getReadStocktakeQueryKey(stocktake.id), saved);
      void queries.invalidateQueries({ queryKey: getListStocktakesQueryKey() });
      void queries.invalidateQueries({ queryKey: getListAdjustmentsQueryKey() });
    } catch (error) {
      toast.error(errorMessage(error, t, "stocktakes.confirmFailed"));
    }
  };

  return (
    <TableRow>
      <TableCell>{name}</TableCell>
      <TableCell className="text-right tabular-nums">
        {quantity(row.expected ?? row.on_hand)}
      </TableCell>
      <TableCell>
        <form onSubmit={submit} className="flex items-center gap-2">
          <Input
            ref={field}
            required
            inputMode="decimal"
            className="w-28"
            aria-label={t("stocktakes.countedFor", { name })}
            value={counted}
            disabled={read_only}
            onChange={(event) => setCounted(event.target.value)}
          />
          <Button
            type="submit"
            size="sm"
            variant={row.counted === null ? "default" : "outline"}
            disabled={read_only || confirm.isPending || counted === ""}
          >
            <Check aria-hidden />
            {row.counted === null ? t("stocktakes.confirm") : t("stocktakes.recount")}
          </Button>
        </form>
      </TableCell>
      <TableCell className="text-right tabular-nums">
        {variance === null
          ? ""
          : variance === 0
            ? t("stocktakes.matched")
            : quantity(String(variance))}
      </TableCell>
    </TableRow>
  );
}
