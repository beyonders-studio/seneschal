import { useTranslation } from "react-i18next";

import type { ItemUsage, ItemUsedIn } from "@/api/generated/hoard.schemas";
import { useQuantity } from "@/features/items/format";
import { StatusBadge } from "@/features/items/ItemList";
import { OutputLink } from "@/features/recipes/RecipesPage";
import { useSettingsNames } from "@/features/settings/names";
import { Badge } from "@/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** What an item's purchase lines not yet received will bring, in `unit`,
 *  marked while a purchase bringing some of it is overdue. */
export function OnOrder({
  item,
  unit,
}: {
  item: { on_order: string; overdue: boolean };
  unit: string;
}) {
  const { t } = useTranslation("movements");
  const quantity = useQuantity();
  return (
    <span className="inline-flex items-center gap-2 tabular-nums">
      {quantity(item.on_order)} {unit}
      {item.overdue && <Badge variant="warning">{t("purchases.overdue")}</Badge>}
    </span>
  );
}

/** What an item received by purchases and what production runs used of it,
 *  in `unit`, over the last week, 1, 3, 6 and 12 months, and all time. */
export function UsageTable({ usage, unit }: { usage: ItemUsage[]; unit: string }) {
  const { t } = useTranslation("catalog");
  const quantity = useQuantity();
  const withUnit = (amount: string) => [quantity(amount), unit].filter(Boolean).join(" ");
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("items.usage.window")}</TableHead>
          <TableHead className="text-right">{t("items.usage.received")}</TableHead>
          <TableHead className="text-right">{t("items.usage.used")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {usage.map((row) => (
          <TableRow key={row.window}>
            <TableCell>{t(`items.usage.windows.${row.window}`)}</TableCell>
            <TableCell className="text-right tabular-nums">{withUnit(row.received)}</TableCell>
            <TableCell className="text-right tabular-nums">{withUnit(row.used)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** The recipes holding an item: what each makes, marked when that is
 *  archived, how much one batch takes of the item in the unit it was written
 *  in, and what a batch makes. */
export function UsedInTable({ usedIn }: { usedIn: ItemUsedIn[] }) {
  const { t } = useTranslation("catalog");
  const quantity = useQuantity();
  const { unit } = useSettingsNames();
  const withUnit = (amount: string, unitId: number | null) =>
    [quantity(amount), unitId === null ? "" : unit(unitId)].filter(Boolean).join(" ");
  if (usedIn.length === 0)
    return <p className="text-sm text-muted-foreground">{t("items.usedIn.none")}</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("items.usedIn.recipe")}</TableHead>
          <TableHead className="text-right">{t("items.usedIn.takes")}</TableHead>
          <TableHead className="text-right">{t("items.usedIn.makes")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {usedIn.map((used) => (
          <TableRow key={used.recipe_id}>
            <TableCell>
              <span className="inline-flex items-center gap-2">
                <OutputLink recipe={used} />
                {used.archived && <StatusBadge status="archived" />}
              </span>
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {withUnit(used.amount, used.unit_id)}
            </TableCell>
            <TableCell className="text-right tabular-nums">
              {withUnit(used.batch_quantity, used.batch_unit_id)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
