import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { ItemStockRead } from "@/api/generated/hoard.schemas";
import { useQuantity } from "@/features/items/format";
import { useSettingsNames } from "@/features/settings/names";
import { useMemberNames } from "@/lib/members";
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

/** What is on hand at each location: of one item, or of each of a product's variations. */
export function StockTable({
  stock,
  unit,
}: {
  stock: OfVariation<ItemStockRead>[];
  unit?: string;
}) {
  const { t } = useTranslation("catalog");
  const quantity = useQuantity();
  const { location } = useSettingsNames();
  if (stock.length === 0)
    return <p className="text-sm text-muted-foreground">{t("items.noStock")}</p>;
  const ofVariations = stock.some((row) => row.variation !== undefined);
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {ofVariations && <TableHead>{t("products.variation")}</TableHead>}
          <TableHead>{t("hoard:common.location")}</TableHead>
          <TableHead className="text-right">{t("hoard:common.onHand")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {stock.map((row) => (
          <TableRow key={`${row.variation}-${row.location_id}`}>
            {ofVariations && <TableCell>{row.variation}</TableCell>}
            <TableCell>{location(row.location_id)}</TableCell>
            <TableCell className="text-right tabular-nums">
              {[quantity(row.quantity), unit].filter(Boolean).join(" ")}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
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
