import { useQueryClient } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import { getListAdjustmentsQueryKey } from "@/api/generated/adjustments/adjustments";
import type { StocktakeRead, StocktakeRowRead } from "@/api/generated/hoard.schemas";
import {
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
  const { date, location, category } = useMovementLabels();
  const progress = useStocktakeProgress();
  const memberName = useMemberNames([stocktake.created_by]);
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
        <p className="text-muted-foreground text-sm">
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
        <p className="text-muted-foreground text-xs">
          {t("hoard:common.createdBy", { name: memberName(stocktake.created_by) })}
        </p>
      </div>
      {stocktake.rows.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t("stocktakes.noRows")}</p>
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
              <StocktakeRowLine key={row.id} stocktake={stocktake} row={row} />
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}

function StocktakeRowLine({ stocktake, row }: { stocktake: StocktakeRead; row: StocktakeRowRead }) {
  const { t } = useTranslation("movements");
  const queries = useQueryClient();
  const { read_only } = useSession();
  const { item, quantity } = useMovementLabels();
  const [counted, setCounted] = useState(row.counted ?? "");
  const confirm = useConfirmStocktakeRow();
  const name = item(row.item);
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
