import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type HoardPropertyRead,
  PropertyType,
  RecordKind,
  type ReportBalanceRow,
} from "@/api/generated/hoard.schemas";
import { useListProperties } from "@/api/generated/properties/properties";
import {
  useReadCostOfGoodsReport,
  useReadMassBalanceReport,
} from "@/api/generated/reports/reports";
import { today } from "@/features/adjustments/labels";
import { useQuantity } from "@/features/items/format";
import { errorMessage } from "@/lib/errors";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** The Select value for nothing chosen: no option's value starts with "_". */
const NONE = "_none";

/** A balance row's quantities, in the order its table shows them. */
const MOVEMENTS = ["opening", "received", "made", "used", "sold", "lost", "closing"] as const;

/** What the certified volume shows: what came in against what was sold. */
const VOLUMES = ["received", "made", "sold"] as const;

type Movement = (typeof MOVEMENTS)[number];

interface Period {
  start: string;
  end: string;
}

/** Reports over a period, in Reports: what the goods cost (for those who see
 *  costs), the mass balance, and certified volume in against sold. The period
 *  runs from the first of the year to today until the reader picks another. */
export function CostOfGoodsSection() {
  const { t } = useTranslation("reports");
  const seesCosts = useSeesCosts();
  const [end, setEnd] = useState(today);
  const [start, setStart] = useState(() => `${end.slice(0, 4)}-01-01`);
  const valid = Boolean(start && end) && start <= end;

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h2 className="text-lg font-semibold tracking-tight">{t("period.title")}</h2>
        <div className="flex flex-wrap gap-3">
          <Field label={t("period.from")}>
            <Input type="date" value={start} onChange={(event) => setStart(event.target.value)} />
          </Field>
          <Field label={t("period.to")}>
            <Input type="date" value={end} onChange={(event) => setEnd(event.target.value)} />
          </Field>
        </div>
      </div>
      {valid ? (
        <>
          {seesCosts && <CostOfGoodsReport start={start} end={end} />}
          <MassBalanceReport start={start} end={end} />
          <CertifiedVolumeReport start={start} end={end} />
        </>
      ) : (
        <p className="text-sm text-muted-foreground">{t("period.invalid")}</p>
      )}
    </section>
  );
}

function Report({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
        {actions && <div className="flex flex-wrap gap-3 pt-2">{actions}</div>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function CostOfGoodsReport({ start, end }: Period) {
  const { t } = useTranslation("reports");
  const money = useMoney();
  const { data, error, isError, refetch } = useReadCostOfGoodsReport({ start, end });

  let body: ReactNode;
  if (isError) {
    body = (
      <StatusMessage description={errorMessage(error, t, "loadError")} onRetry={() => refetch()} />
    );
  } else if (!data) {
    body = <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;
  } else {
    const lines: [string, string][] = [
      [t("costOfGoods.opening"), money(data.opening)],
      [t("costOfGoods.starting"), money(data.starting)],
      [t("costOfGoods.purchases"), money(data.purchases)],
      [t("costOfGoods.personalUse"), money(data.personal_use)],
      [t("costOfGoods.closing"), money(data.closing)],
    ];
    body = (
      <div className="flex flex-col gap-4">
        <Table>
          <TableBody>
            {lines.map(([label, amount]) => (
              <TableRow key={label}>
                <TableCell>{label}</TableCell>
                <TableCell className="text-end tabular-nums">{amount}</TableCell>
              </TableRow>
            ))}
            <TableRow>
              <TableCell className="font-semibold">{t("costOfGoods.total")}</TableCell>
              <TableCell className="text-end font-semibold tabular-nums">
                {money(data.cost_of_goods)}
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
        <p className="text-sm text-muted-foreground">
          {t("costOfGoods.shipped", { amount: money(data.shipped) })}
        </p>
        {data.uncosted_runs > 0 && (
          <p className="text-sm text-destructive">
            {t("costOfGoods.uncosted", { count: data.uncosted_runs })}
          </p>
        )}
      </div>
    );
  }
  return (
    <Report title={t("costOfGoods.title")} description={t("costOfGoods.description")}>
      {body}
    </Report>
  );
}

/** The lot properties a balance splits by: the selects. */
function useLotSelects(): HoardPropertyRead[] {
  const { data } = useListProperties({ kind: RecordKind.lot });
  return (data ?? []).filter((property) => property.type === PropertyType.select);
}

function Choice({
  label,
  none,
  value,
  onChange,
  choices,
}: {
  label: string;
  none: string;
  value: string;
  onChange: (value: string) => void;
  choices: { value: string; label: string }[];
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-48" aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE}>{none}</SelectItem>
        {choices.map((choice) => (
          <SelectItem key={choice.value} value={choice.value}>
            {choice.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

const propertyChoices = (properties: HoardPropertyRead[]) =>
  properties.map((property) => ({ value: String(property.id), label: property.name }));

/** A balance's rows, or those under `only` of the property it is split by,
 *  with `columns`; or what to show while they load, or when there are none. */
function BalanceTable({
  period,
  split,
  only,
  columns,
  empty,
}: {
  period: Period;
  split: HoardPropertyRead | undefined;
  only?: string;
  columns: readonly Movement[];
  empty: string;
}) {
  const { t } = useTranslation("reports");
  const quantity = useQuantity();
  const { data, error, isError, refetch } = useReadMassBalanceReport({
    ...period,
    split_by: split?.id,
  });

  if (isError) {
    return (
      <StatusMessage description={errorMessage(error, t, "loadError")} onRetry={() => refetch()} />
    );
  }
  if (!data) return <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;
  const rows = only === undefined ? data : data.filter((row) => row.option === only);
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>;
  const showsOption = split !== undefined && only === undefined;
  const optionLabel = (row: ReportBalanceRow) =>
    split?.options?.find((option) => option.value === row.option)?.label ??
    row.option ??
    t("massBalance.noOption");

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("hoard:common.name")}</TableHead>
          {showsOption && <TableHead>{split.name}</TableHead>}
          <TableHead>{t("massBalance.unit")}</TableHead>
          {columns.map((column) => (
            <TableHead key={column} className="text-end">
              {t(`massBalance.${column}`)}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => {
          const attributes = Object.values(row.attributes).join(", ");
          return (
            <TableRow key={`${row.item_id}-${row.option ?? NONE}`}>
              <TableCell>
                {row.name}
                {attributes && (
                  <span className="ms-2 text-sm text-muted-foreground">{attributes}</span>
                )}
              </TableCell>
              {showsOption && <TableCell>{optionLabel(row)}</TableCell>}
              <TableCell>{row.unit ?? t("massBalance.each")}</TableCell>
              {columns.map((column) => (
                <TableCell key={column} className="text-end tabular-nums">
                  {quantity(row[column])}
                </TableCell>
              ))}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

function MassBalanceReport(period: Period) {
  const { t } = useTranslation("reports");
  const properties = useLotSelects();
  const [splitBy, setSplitBy] = useState(NONE);
  const split = properties.find((property) => String(property.id) === splitBy);

  return (
    <Report
      title={t("massBalance.title")}
      description={t("massBalance.description")}
      actions={
        properties.length > 0 && (
          <Choice
            label={t("massBalance.splitBy")}
            none={t("massBalance.notSplit")}
            value={split ? splitBy : NONE}
            onChange={setSplitBy}
            choices={propertyChoices(properties)}
          />
        )
      }
    >
      <BalanceTable
        period={period}
        split={split}
        columns={MOVEMENTS}
        empty={t("massBalance.empty")}
      />
    </Report>
  );
}

function CertifiedVolumeReport(period: Period) {
  const { t } = useTranslation("reports");
  const properties = useLotSelects();
  const [propertyId, setPropertyId] = useState(NONE);
  const [optionValue, setOptionValue] = useState(NONE);
  const property = properties.find((one) => String(one.id) === propertyId);
  const option = property?.options?.find((one) => one.value === optionValue);

  let body: ReactNode;
  if (properties.length === 0) {
    body = <p className="text-sm text-muted-foreground">{t("certifiedVolume.noProperties")}</p>;
  } else if (!property || !option) {
    body = <p className="text-sm text-muted-foreground">{t("certifiedVolume.choose")}</p>;
  } else {
    body = (
      <BalanceTable
        period={period}
        split={property}
        only={option.value}
        columns={VOLUMES}
        empty={t("certifiedVolume.empty")}
      />
    );
  }
  return (
    <Report
      title={t("certifiedVolume.title")}
      description={t("certifiedVolume.description")}
      actions={
        properties.length > 0 && (
          <>
            <Choice
              label={t("certifiedVolume.property")}
              none={t("certifiedVolume.chooseProperty")}
              value={property ? propertyId : NONE}
              onChange={(next) => {
                setPropertyId(next);
                setOptionValue(NONE);
              }}
              choices={propertyChoices(properties)}
            />
            {property && (
              <Choice
                label={t("certifiedVolume.option")}
                none={t("certifiedVolume.chooseOption")}
                value={option ? optionValue : NONE}
                onChange={setOptionValue}
                choices={property.options ?? []}
              />
            )}
          </>
        )
      }
    >
      {body}
    </Report>
  );
}
