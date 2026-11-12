import { useNavigate } from "@tanstack/react-router";
import { CookingPot } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useListBases } from "@/api/generated/bases/bases";
import { BaseDialog } from "@/features/bases/BaseDialog";
import { BASES } from "@/features/bases/fields";
import { LayoutsButton, RecordTable } from "@/features/fields/records";
import { ItemList, useItemSearch } from "@/features/items/ItemList";

/** The bases list, under Stock. */
export function BasesPage() {
  const { t } = useTranslation("catalog");
  const navigate = useNavigate();
  const search = useItemSearch();
  const bases = useListBases(search.params);
  const [adding, setAdding] = useState(false);

  return (
    <>
      <ItemList
        search={search}
        total={bases.data?.total}
        addLabel={t("bases.add")}
        onAdd={() => setAdding(true)}
        empty={{
          icon: <CookingPot />,
          title: t("bases.emptyTitle"),
          description: t("bases.emptyBody"),
        }}
        failed={bases.isError}
        onRetry={() => void bases.refetch()}
        more={<LayoutsButton of={BASES} rows={bases.data?.items ?? []} />}
      >
        <RecordTable of={BASES} items={bases.data?.items ?? []} />
        {bases.data?.items.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("items.noMatch")}</p>
        )}
      </ItemList>
      <BaseDialog
        open={adding}
        onOpenChange={setAdding}
        onSaved={(base) =>
          void navigate({ to: "/stock/bases/$baseId", params: { baseId: String(base.id) } })
        }
      />
    </>
  );
}
