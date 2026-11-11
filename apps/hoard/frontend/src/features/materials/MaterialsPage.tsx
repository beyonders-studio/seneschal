import { useNavigate } from "@tanstack/react-router";
import { Wheat } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useListMaterials } from "@/api/generated/materials/materials";
import { LayoutsButton, RecordTable } from "@/features/fields/records";
import { ItemList, useItemSearch } from "@/features/items/ItemList";
import { MATERIALS } from "@/features/materials/fields";
import { MaterialDialog } from "@/features/materials/MaterialDialog";

/** The materials list, under Stock. */
export function MaterialsPage() {
  const { t } = useTranslation("catalog");
  const navigate = useNavigate();
  const search = useItemSearch();
  const materials = useListMaterials(search.params);
  const [adding, setAdding] = useState(false);

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
        more={<LayoutsButton of={MATERIALS} rows={materials.data?.items ?? []} />}
      >
        <RecordTable of={MATERIALS} items={materials.data?.items ?? []} />
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
