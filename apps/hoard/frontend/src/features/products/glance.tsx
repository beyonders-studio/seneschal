import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { ProductDetail, SalesGroupRead } from "@/api/generated/hoard.schemas";
import { useQuantity, useShare } from "@/features/items/format";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { Badge } from "@/ui/badge";
import { cn } from "@/ui/lib/utils";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** One figure: its label over its value. */
function Tile({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border p-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums">{children}</dd>
    </div>
  );
}

/** A titled row of figures. */
function Tiles({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <h4 className="text-sm font-medium">{title}</h4>
      <dl className="grid grid-cols-fill-32/5 gap-3">{children}</dl>
    </div>
  );
}

/**
 * A product at a glance, its variations added up: its stock (on hand, what
 * consignees hold, once they hold some, what open orders commit, what is available, what its recipe could make and what
 * is on order) and, to date, what it made, sold and shipped. A bundle holds
 * no stock of its own: what is available of it is what its contents make
 * together.
 */
export function Glance({ product }: { product: ProductDetail }) {
  const { t } = useTranslation("catalog");
  const quantity = useQuantity();
  const { totals, bundle } = product;
  return (
    <div className="flex flex-col gap-4">
      <Tiles title={t("products.glance.stock")}>
        {!bundle && <Tile label={t("hoard:common.onHand")}>{quantity(product.on_hand)}</Tile>}
        {Number(totals.consigned) > 0 && (
          <Tile label={t("items.consigned")}>{quantity(totals.consigned)}</Tile>
        )}
        <Tile label={t("products.glance.committed")}>{quantity(totals.committed)}</Tile>
        <Tile label={t("products.glance.available")}>{quantity(totals.available)}</Tile>
        {!bundle && totals.potential !== null && (
          <Tile label={t("items.potential")}>{quantity(totals.potential)}</Tile>
        )}
        {!bundle && (
          <Tile label={t("items.onOrder")}>
            <span className="inline-flex items-center gap-2">
              {quantity(totals.on_order)}
              {totals.overdue && (
                <Badge variant="warning">{t("movements:purchases.overdue")}</Badge>
              )}
            </span>
          </Tile>
        )}
      </Tiles>
      <Tiles title={t("products.glance.toDate")}>
        {!bundle && <Tile label={t("items.made")}>{quantity(totals.made)}</Tile>}
        <Tile label={t("products.glance.sold")}>{quantity(totals.sold)}</Tile>
        <Tile label={t("products.glance.shipped")}>{quantity(totals.shipped)}</Tile>
      </Tiles>
    </div>
  );
}

/**
 * What a product's order lines earned and what what they shipped cost, its
 * variations added up, to a member who sees costs: what sold, the revenue,
 * the gross profit left and its share of the revenue, marked when it is a
 * loss, for retail and wholesale customers apart, or in one row while nobody
 * bought wholesale. It says when a shipment went out with no cost, and how
 * many orders in another currency it leaves out.
 */
export function Profitability({ product }: { product: ProductDetail }) {
  const { t } = useTranslation("catalog");
  const quantity = useQuantity();
  const money = useMoney();
  const share = useShare();
  const { totals } = product;
  const profit = totals.profit;
  if (!useSeesCosts() || !profit) return null;
  const groups: [string, SalesGroupRead][] = totals.wholesale
    ? [
        [t("products.profit.retail"), totals.retail],
        [t("products.profit.wholesale"), totals.wholesale],
      ]
    : [[t("products.profit.all"), totals.retail]];
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">{t("products.profit.hint")}</p>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("products.profit.group")}</TableHead>
            <TableHead className="text-right">{t("products.glance.sold")}</TableHead>
            <TableHead className="text-right">{t("products.profit.revenue")}</TableHead>
            <TableHead className="text-right">{t("products.profit.profit")}</TableHead>
            <TableHead className="text-right">{t("products.profit.share")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {groups.map(([label, group]) => {
            const earned = group.profit;
            const loss = earned !== null && Number(earned.profit) < 0;
            return (
              <TableRow key={label}>
                <TableCell>{label}</TableCell>
                <TableCell className="text-right tabular-nums">{quantity(group.sold)}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {earned && money(earned.revenue)}
                </TableCell>
                <TableCell className={cn("text-right tabular-nums", loss && "text-destructive")}>
                  {earned && money(earned.profit)}
                </TableCell>
                <TableCell className={cn("text-right tabular-nums", loss && "text-destructive")}>
                  {earned?.share == null ? (
                    <span className="text-muted-foreground">{t("products.pricing.unknown")}</span>
                  ) : (
                    share(earned.share)
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {profit.uncosted > 0 && (
        <p className="inline-flex items-center gap-2 text-sm">
          <Badge variant="warning">{t("products.profit.uncostedMark")}</Badge>
          {t("products.profit.uncosted", { count: profit.uncosted })}
        </p>
      )}
      {profit.left_out > 0 && (
        <p className="text-sm text-muted-foreground">
          {t("products.profit.leftOut", { count: profit.left_out })}
        </p>
      )}
    </div>
  );
}
