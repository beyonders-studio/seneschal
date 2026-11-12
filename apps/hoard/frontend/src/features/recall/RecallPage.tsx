import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Download, Undo2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type ReadRecallParams,
  type TraceRead,
  TraceRecallFormat,
  type TraceStepRead,
} from "@/api/generated/hoard.schemas";
import { useReadRecall } from "@/api/generated/trace/trace";
import { ItemField } from "@/features/adjustments/fields";
import { useMovementLabels } from "@/features/adjustments/labels";
import { LotLink, MayHaveBadge, StepsTable } from "@/features/trace/StepsTable";
import { errorMessage } from "@/lib/errors";
import { Button } from "@/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";
import { StatusMessage } from "@/ui/status-message";

/** Where a recall downloads as `format`. */
const exportHref = (params: ReadRecallParams, format: TraceRecallFormat) => {
  const query = new URLSearchParams({ format });
  for (const [name, value] of Object.entries(params))
    if (value !== null && value !== undefined) query.set(name, String(value));
  return `/api/v1/trace/recall/export?${query}`;
};

/** The lots a trace's runs made, once each, recorded before assumed. */
function madeLots(steps: TraceStepRead[]): TraceStepRead[] {
  const seen = new Set<number>();
  return [...steps]
    .filter((step) => step.kind === "run" && step.made_lot)
    .sort((a, b) => Number(a.assumed) - Number(b.assumed))
    .filter((step) => {
      const id = step.made_lot?.id ?? 0;
      if (seen.has(id)) return false;
      seen.add(id);
      return true;
    });
}

/** One recalled lot: the lots made from it and the shipments it reached. */
function TraceCard({ trace }: { trace: TraceRead }) {
  const { t } = useTranslation("trace");
  const labels = useMovementLabels();
  const made = madeLots(trace.steps);
  const shipped = trace.steps
    .filter((step) => step.kind === "shipment")
    .sort((a, b) => Number(a.assumed) - Number(b.assumed));
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <span className="inline-flex flex-wrap items-center gap-2">
            {t("recall.lotTitle", { item: labels.item(trace.item) })}
            <LotLink lot={trace.lot} />
          </span>
        </CardTitle>
        <CardDescription>{labels.date(trace.dated_on)}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{t("recall.made")}</h3>
          {made.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("recall.madeEmpty")}</p>
          ) : (
            <ul className="flex flex-col gap-1 text-sm">
              {made.map((step) => (
                <li key={step.made_lot?.id} className="flex flex-wrap items-center gap-2">
                  <LotLink lot={step.made_lot} />
                  <span className="text-muted-foreground">{labels.date(step.dated_on)}</span>
                  {step.assumed && <MayHaveBadge />}
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{t("recall.shipments")}</h3>
          <StepsTable steps={shipped} empty={t("recall.shipmentsEmpty")} />
        </section>
      </CardContent>
    </Card>
  );
}

function RecallResults({ params }: { params: ReadRecallParams }) {
  const { t } = useTranslation("trace");
  const { data, error, isError, refetch } = useReadRecall(params);
  if (isError)
    return (
      <StatusMessage
        description={errorMessage(error, t, "recall.loadError")}
        onRetry={() => void refetch()}
      />
    );
  if (!data) return <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;
  if (data.traces.length === 0)
    return <p className="text-sm text-muted-foreground">{t("recall.none")}</p>;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted-foreground">{t("recall.download")}</span>
        {Object.values(TraceRecallFormat).map((format) => (
          <Button key={format} asChild variant="outline" size="sm">
            <a href={exportHref(params, format)} download>
              <Download aria-hidden />
              {t(`recall.formats.${format}`)}
            </a>
          </Button>
        ))}
      </div>
      {data.traces.map((trace) => (
        <TraceCard key={trace.lot.id} trace={trace} />
      ))}
    </div>
  );
}

/** A recall, under Stock: from a lot, or from an item and a date range,
 *  every lot made from it and every shipment it reached or may have, with
 *  the files a regulator asks for. */
export function RecallPage() {
  const { t } = useTranslation("trace");
  const { lot } = useSearch({ from: "/stock/recall" });
  const navigate = useNavigate();
  const [item, setItem] = useState<number | null>(null);
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [asked, setAsked] = useState<ReadRecallParams | null>(null);
  const params: ReadRecallParams | null = lot ? { lot_id: lot } : asked;

  const recall = (event: FormEvent) => {
    event.preventDefault();
    if (item === null) return;
    setAsked({ item_id: item, since: since || undefined, until: until || undefined });
  };

  return (
    <div className="flex flex-col gap-6">
      {lot ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-muted-foreground">{t("recall.fromLot")}</p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void navigate({ to: "/stock/recall", search: {} })}
          >
            {t("recall.byItem")}
          </Button>
        </div>
      ) : (
        <form onSubmit={recall} className="flex flex-col gap-4">
          <ItemField label={t("recall.item")} value={item} onChange={setItem} />
          <div className="grid grid-cols-pair gap-4">
            <Field label={t("recall.since")}>
              <Input type="date" value={since} onChange={(event) => setSince(event.target.value)} />
            </Field>
            <Field label={t("recall.until")}>
              <Input type="date" value={until} onChange={(event) => setUntil(event.target.value)} />
            </Field>
          </div>
          <div>
            <Button type="submit" disabled={item === null}>
              {t("recall.find")}
            </Button>
          </div>
        </form>
      )}
      {params ? (
        <RecallResults params={params} />
      ) : (
        <StatusMessage
          icon={<Undo2 />}
          title={t("recall.emptyTitle")}
          description={t("recall.emptyBody")}
        />
      )}
      {lot && (
        <p className="text-sm">
          <Link to="/stock/lots/$lotId" params={{ lotId: String(lot) }} className="hover:underline">
            {t("recall.backToLot")}
          </Link>
        </p>
      )}
    </div>
  );
}
