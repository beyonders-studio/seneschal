import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import type { ItemLotRead, LotRead } from "@/api/generated/hoard.schemas";
import { DeclarationsPanel } from "@/features/declarations/DeclarationsPanel";
import { DocumentsSection } from "@/features/documents/DocumentsSection";
import { builtin, type HoardKind, RecordTable } from "@/features/fields/records";
import { HoldSection } from "@/features/holds/HoldSection";
import { useQuantity } from "@/features/items/format";
import { RecordStamps } from "@/features/items/Holdings";
import { useSettingsNames } from "@/features/settings/names";
import { TraceSection } from "@/features/trace/TraceSection";
import { Badge } from "@/ui/badge";
import { at, detailLayoutSpec } from "@/ui/layouts/detailLayout";
import { formatDate } from "@/ui/lib/formatDate";

/** A lot's number, which opens its page. */
function LotNumber({ item }: { item: ItemLotRead }) {
  return (
    <Link
      to="/stock/lots/$lotId"
      params={{ lotId: String(item.id) }}
      className="font-medium underline-offset-4 hover:underline"
    >
      {item.number}
    </Link>
  );
}

const Location = ({ item }: { item: ItemLotRead }) => useSettingsNames().location(item.location_id);
const Balance = ({ item }: { item: ItemLotRead }) => (
  <span className="inline-flex items-center gap-2 tabular-nums">
    {item.on_hold && <OnHoldBadge />}
    {useQuantity()(item.balance)}
  </span>
);

/** Marks a lot on hold. */
export function OnHoldBadge() {
  const { t } = useTranslation("holds");
  return <Badge variant="outline">{t("section.badge")}</Badge>;
}

const field = (id: string) => ({ type: "field", props: { field: id } });

/** Lots, as an item's page lists them and a lot's own page shows one. */
export const LOTS: HoardKind<ItemLotRead, LotRead> = {
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
      "hold",
      "holds:section.title",
      (lot) => lot.hold?.id,
      ({ item }) => <HoldSection lot={item} />
    ),
    builtin(
      "trace",
      "trace:section.title",
      (lot) => lot.id,
      ({ item }) => <TraceSection lot={item} />
    ),
    builtin(
      "declarations",
      "declarations:panel.title",
      (lot) => lot.id,
      ({ item }) => <DeclarationsPanel of="lot" id={item.id} />
    ),
    builtin(
      "documents",
      "documents:section.title",
      (lot) => lot.id,
      ({ item }) => <DocumentsSection of="lot" id={item.id} />
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
        at(3, field("hold")),
        at(4, field("trace")),
        at(5, field("declarations")),
        at(6, field("documents")),
        at(7, { type: "byline" }),
      ],
      side: [at(2, { type: "section", children: [{ type: "properties" }] })],
    },
    fieldParts: ["field", "properties"],
  }),
  parts: { byline: (_node, item) => <RecordStamps record={item} /> },
  href: (lot) => `/stock/lots/${lot.id}`,
};

/** An item's lots, oldest first, with what is left of each. */
export function LotsTable({ lots }: { lots: ItemLotRead[] }) {
  const { t } = useTranslation("catalog");
  if (lots.length === 0)
    return <p className="text-sm text-muted-foreground">{t("items.noLots")}</p>;
  return <RecordTable of={LOTS} items={lots} />;
}
