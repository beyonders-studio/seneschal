import { MapPin, Pencil } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import type { ItemStockRead } from "@/api/generated/hoard.schemas";
import { BinDialog, useBinLabel } from "@/features/items/bins";
import { useQuantity } from "@/features/items/format";
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
 * variations. With `bins`, each location's bin for the item too, which is set
 * here while Hoard may change.
 */
export function StockTable({
  stock,
  unit,
  bins,
}: {
  stock: OfVariation<ItemStockRead>[];
  unit?: string;
  bins?: { itemId: number; onChanged: () => void };
}) {
  const { t } = useTranslation("catalog");
  const quantity = useQuantity();
  const binLabel = useBinLabel();
  const { read_only } = useSession();
  const { location } = useSettingsNames();
  // The row whose bin is being set, or null for a location picked in the dialog.
  const [binning, setBinning] = useState<ItemStockRead | null | undefined>(undefined);
  const editsBins = bins !== undefined && !read_only;
  const ofVariations = stock.some((row) => row.variation !== undefined);
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
                <TableCell className="text-right tabular-nums">
                  {[quantity(row.quantity), unit].filter(Boolean).join(" ")}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {editsBins && (
        <>
          <Button
            variant="outline"
            size="sm"
            className="self-start"
            onClick={() => setBinning(null)}
          >
            <MapPin aria-hidden />
            {t("items.bin.add")}
          </Button>
          <BinDialog
            itemId={bins.itemId}
            location={binning?.location_id ?? null}
            bin={binning?.bin ?? null}
            open={binning !== undefined}
            onOpenChange={(open) => !open && setBinning(undefined)}
            onSaved={bins.onChanged}
          />
        </>
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
