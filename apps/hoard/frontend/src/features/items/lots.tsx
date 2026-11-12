import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { ItemLotRead } from "@/api/generated/hoard.schemas";
import { DeclarationsPanel } from "@/features/declarations/DeclarationsPanel";
import {
  builtin,
  type HoardKind,
  LayoutsButton,
  RecordDetail,
  RecordTable,
} from "@/features/fields/records";
import { useQuantity } from "@/features/items/format";
import { useSettingsNames } from "@/features/settings/names";
import { Button } from "@/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/ui/dialog";
import { at, detailLayoutSpec } from "@/ui/layouts/detailLayout";
import { formatDate } from "@/ui/lib/formatDate";

/** A lot's number, which opens the lot: its fields and properties. */
function LotNumber({ item }: { item: ItemLotRead }) {
  const { t } = useTranslation("fields");
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="link" className="h-auto p-0 font-medium" onClick={() => setOpen(true)}>
        {item.number}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader className="flex-row flex-wrap items-center justify-between gap-2 pr-8">
            <DialogTitle>{t("lot", { number: item.number })}</DialogTitle>
            <LayoutsButton of={LOTS} rows={[item]} page={item} />
          </DialogHeader>
          <RecordDetail
            of={LOTS}
            item={item}
            // A lot is read with the item it is of.
            onSaved={() =>
              void queryClient.invalidateQueries({
                predicate: ({ queryKey: [path] }) =>
                  typeof path === "string" && /^\/api\/v1\/(materials|products)\/\d+$/.test(path),
              })
            }
          />
        </DialogContent>
      </Dialog>
    </>
  );
}

const Location = ({ item }: { item: ItemLotRead }) => useSettingsNames().location(item.location_id);
const Balance = ({ item }: { item: ItemLotRead }) => (
  <span className="tabular-nums">{useQuantity()(item.balance)}</span>
);

const field = (id: string) => ({ type: "field", props: { field: id } });

/** Lots, as an item's page lists them and a lot opened from there shows one. */
export const LOTS: HoardKind<ItemLotRead> = {
  kind: "lot",
  fields: [
    builtin("number", "catalog:items.lot", (lot) => lot.number, LotNumber),
    builtin("location", "hoard:common.location", (lot) => lot.location_id, Location),
    builtin(
      "dated_on",
      "hoard:common.date",
      (lot) => lot.dated_on,
      ({ item }) => formatDate(item.dated_on)
    ),
    builtin(
      "expires_on",
      "catalog:items.expires",
      (lot) => lot.expires_on,
      ({ item }) => formatDate(item.expires_on)
    ),
    builtin("balance", "catalog:items.balance", (lot) => lot.balance, Balance),
    builtin(
      "notes",
      "hoard:common.notes",
      (lot) => lot.notes,
      ({ item }) => item.notes
    ),
  ],
  pageFields: [
    builtin(
      "declarations",
      "declarations:panel.title",
      (lot) => lot.id,
      ({ item }) => <DeclarationsPanel of="lot" id={item.id} />
    ),
  ],
  columns: ["number", "location", "dated_on", "expires_on", "balance", "notes"],
  spec: detailLayoutSpec({
    kind: "lot",
    shipped: {
      header: [],
      main: [
        at(1, {
          type: "section",
          children: ["location", "dated_on", "expires_on", "balance", "notes"].map(field),
        }),
        at(3, field("declarations")),
      ],
      side: [at(2, { type: "section", children: [{ type: "properties" }] })],
    },
    fieldParts: ["field", "properties"],
  }),
  href: () => "",
};

/** An item's lots, oldest first, with what is left of each. */
export function LotsTable({ lots }: { lots: ItemLotRead[] }) {
  const { t } = useTranslation("catalog");
  if (lots.length === 0)
    return <p className="text-sm text-muted-foreground">{t("items.noLots")}</p>;
  return <RecordTable of={LOTS} items={lots} />;
}
