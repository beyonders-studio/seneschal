import { Link, useNavigate } from "@tanstack/react-router";
import { Wheat } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useListMaterials } from "@/api/generated/materials/materials";
import { isLow, useQuantity } from "@/features/items/format";
import { ItemList, LowStockBadge, useItemSearch } from "@/features/items/ItemList";
import { MaterialDialog } from "@/features/materials/MaterialDialog";
import { useSettingsNames } from "@/features/settings/names";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** The materials list, under Stock. */
export function MaterialsPage() {
  const { t } = useTranslation("catalog");
  const navigate = useNavigate();
  const search = useItemSearch();
  const materials = useListMaterials(search.params);
  const [adding, setAdding] = useState(false);
  const names = useSettingsNames();
  const quantity = useQuantity();
  const money = useMoney();
  const seesCosts = useSeesCosts();

  return (
    <>
      <ItemList
        search={search}
        total={materials.data?.total}
        addLabel={t("materials.add")}
        onAdd={() => setAdding(true)}
        empty={{
          icon: <Wheat />,
          title: t("materials.emptyTitle"),
          description: t("materials.emptyBody"),
        }}
        failed={materials.isError}
        onRetry={() => void materials.refetch()}
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("hoard:common.name")}</TableHead>
              <TableHead>{t("hoard:common.sku")}</TableHead>
              <TableHead>{t("hoard:common.category")}</TableHead>
              <TableHead className="text-right">{t("hoard:common.onHand")}</TableHead>
              <TableHead className="text-right">{t("hoard:common.lowStockLimit")}</TableHead>
              {seesCosts && <TableHead className="text-right">{t("items.averageCost")}</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {materials.data?.items.map((material) => (
              <TableRow key={material.id}>
                <TableCell>
                  <Link
                    to="/stock/materials/$materialId"
                    params={{ materialId: String(material.id) }}
                    className="font-medium hover:underline"
                  >
                    {material.name}
                  </Link>
                </TableCell>
                <TableCell>{material.sku}</TableCell>
                <TableCell>{names.category(material.category_id)}</TableCell>
                <TableCell className="text-right tabular-nums">
                  <span className="inline-flex items-center gap-2">
                    {isLow(material.on_hand, material.low_stock_limit) && <LowStockBadge />}
                    {quantity(material.on_hand)} {names.unit(material.tracking_unit_id)}
                  </span>
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {material.low_stock_limit && quantity(material.low_stock_limit)}
                </TableCell>
                {seesCosts && (
                  <TableCell className="text-right tabular-nums">
                    {material.average_cost && money(material.average_cost, { exact: true })}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {materials.data?.items.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("items.noMatch")}</p>
        )}
      </ItemList>
      <MaterialDialog
        open={adding}
        onOpenChange={setAdding}
        onSaved={(material) =>
          void navigate({
            to: "/stock/materials/$materialId",
            params: { materialId: String(material.id) },
          })
        }
      />
    </>
  );
}
