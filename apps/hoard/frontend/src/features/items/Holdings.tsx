import { ArrowRightLeft, MapPin, Pencil } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import type { ItemStockRead } from "@/api/generated/hoard.schemas";
import { useMovementLabels } from "@/features/adjustments/labels";
import { BinDialog, useBinLabel } from "@/features/items/bins";
import { useQuantity } from "@/features/items/format";
import { type Movable, TransferDialog } from "@/features/items/transfer";
import { useSettingsNames } from "@/features/settings/names";
import { useMemberNames } from "@/lib/members";
import { useSession } from "@/session";
import { Button } from "@/ui/button";
import { RelativeTime } from "@/ui/relative-time";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** A row of stock, and the variation it is of on a product's page. */
type OfVariation<T> = T & { variation?: string };

/** One part of an item's page: its heading, then what it holds. */
export function ItemSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-base font-semibold">{title}</h3>
      {children}
    </section>
  );
}

/**
 * What is on hand at each location: of one item, or of each of a product's
 * variations, with what of it consignees hold, when a stocktake last counted
 * it there and, for an item with a recipe, what it could make there. With
 * `bins`, each location's bin for the item too, which is set here while Hoard
 * may change; with `moves`, stock of those items is moved between locations
 * here.
 */
export function StockTable({
  stock,
  unit,
  bins,
  moves,
}: {
  stock: OfVariation<ItemStockRead>[];
  unit?: string;
  bins?: { itemId: number; onChanged: () => void };
  moves?: { items: Movable[]; onChanged: () => void };
}) {
  const { t } = useTranslation("catalog");
  const quantity = useQuantity();
  const { date } = useMovementLabels();
  const binLabel = useBinLabel();
  const { read_only } = useSession();
  const { location } = useSettingsNames();
  // The row whose bin is being set, or null for a location picked in the dialog.
  const [binning, setBinning] = useState<ItemStockRead | null | undefined>(undefined);
  const [moving, setMoving] = useState(false);
  const editsBins = bins !== undefined && !read_only;
  const movesStock =
    moves !== undefined && !read_only && stock.some((row) => Number(row.quantity) > 0);
  const ofVariations = stock.some((row) => row.variation !== undefined);
  const makes = stock.some((row) => row.could_make !== null);
  const consigned = stock.some((row) => Number(row.consigned) > 0);
  const withUnit = (amount: string) => [quantity(amount), unit].filter(Boolean).join(" ");
  return (
    <div className="flex flex-col gap-2">
      {stock.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("items.noStock")}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              {ofVariations && <TableHead>{t("products.variation")}</TableHead>}
              <TableHead>{t("hoard:common.location")}</TableHead>
              {bins && <TableHead>{t("items.bin.title")}</TableHead>}
              <TableHead className="text-right">{t("hoard:common.onHand")}</TableHead>
              {consigned && <TableHead className="text-right">{t("items.consigned")}</TableHead>}
              {makes && <TableHead className="text-right">{t("items.couldMake")}</TableHead>}
              <TableHead>{t("items.lastStocktake")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {stock.map((row) => (
              <TableRow key={`${row.variation}-${row.location_id}`}>
                {ofVariations && <TableCell>{row.variation}</TableCell>}
                <TableCell>{location(row.location_id)}</TableCell>
                {bins && (
                  <TableCell>
                    <span className="inline-flex items-center gap-1">
                      {binLabel(row.bin)}
                      {editsBins && (
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={t("items.bin.edit", { location: location(row.location_id) })}
                          onClick={() => setBinning(row)}
                        >
                          <Pencil aria-hidden />
                        </Button>
                      )}
                    </span>
                  </TableCell>
                )}
                <TableCell className="text-right tabular-nums">{withUnit(row.quantity)}</TableCell>
                {consigned && (
                  <TableCell className="text-right tabular-nums">
                    {Number(row.consigned) > 0 && withUnit(row.consigned)}
                  </TableCell>
                )}
                {makes && (
                  <TableCell className="text-right tabular-nums">
                    {row.could_make !== null && withUnit(row.could_make)}
                  </TableCell>
                )}
                <TableCell>{row.last_stocktake && date(row.last_stocktake)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {(editsBins || movesStock) && (
        <div className="flex flex-wrap gap-2">
          {editsBins && (
            <Button variant="outline" size="sm" onClick={() => setBinning(null)}>
              <MapPin aria-hidden />
              {t("items.bin.add")}
            </Button>
          )}
          {movesStock && (
            <Button variant="outline" size="sm" onClick={() => setMoving(true)}>
              <ArrowRightLeft aria-hidden />
              {t("items.move.title")}
            </Button>
          )}
        </div>
      )}
      {moves && (
        <TransferDialog
          items={moves.items}
          unit={unit}
          open={moving}
          onOpenChange={setMoving}
          onMoved={moves.onChanged}
        />
      )}
      {editsBins && (
        <BinDialog
          itemId={bins.itemId}
          location={binning?.location_id ?? null}
          bin={binning?.bin ?? null}
          open={binning !== undefined}
          onOpenChange={(open) => !open && setBinning(undefined)}
          onSaved={bins.onChanged}
        />
      )}
    </div>
  );
}

interface Stamped {
  created_at: string;
  created_by: string | null;
  updated_at: string;
  updated_by: string | null;
}

/** Who added a record and when, and who last changed it and when. */
export function RecordStamps({ record }: { record: Stamped }) {
  const { t } = useTranslation();
  const name = useMemberNames([record.created_by, record.updated_by]);
  return (
    <p className="text-xs text-muted-foreground">
      {t("common.createdBy", { name: name(record.created_by) })}{" "}
      <RelativeTime date={record.created_at} />
      {" · "}
      {t("common.updatedBy", { name: name(record.updated_by) })}{" "}
      <RelativeTime date={record.updated_at} />
    </p>
  );
}
