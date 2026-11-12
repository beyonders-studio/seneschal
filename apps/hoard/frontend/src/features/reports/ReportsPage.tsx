import { Link } from "@tanstack/react-router";
import { Download } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { ExportFormat, ExportKind } from "@/api/generated/hoard.schemas";
import { useReadReorderReport, useReadValuationReport } from "@/api/generated/reports/reports";
import { CostOfGoodsSection } from "@/features/reports/CostOfGoodsSection";
import { errorMessage } from "@/lib/errors";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { Button } from "@/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";
import { numberFormat } from "@/ui/lib/intl";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** As many places as Hoard keeps a quantity to. */
const QUANTITY_PLACES = 6;

/** Formats a quantity, an exact decimal as the API sends it, in the reader's language. */
function useQuantity(): (amount: string) => string {
  const { i18n } = useTranslation();
  const format = numberFormat(i18n.resolvedLanguage, { maximumFractionDigits: QUANTITY_PLACES });
  return (amount) => format.format(amount as Intl.StringNumericLiteral);
}

/** Reports on stock, inside an initiative: what to reorder, what the stock is
 *  worth (for those who see costs), reports over a period, and every record
 *  to download. */
export function ReportsPage() {
  const seesCosts = useSeesCosts();
  return (
    <div className="flex flex-col gap-6">
      <ReorderReport />
      {seesCosts && <ValuationReport />}
      <CostOfGoodsSection />
      <ExportLinks />
    </div>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function ReorderReport() {
  const { t } = useTranslation("reports");
  const quantity = useQuantity();
  const { data, error, isError, refetch } = useReadReorderReport();

  let body: ReactNode;
  if (isError) {
    body = (
      <StatusMessage description={errorMessage(error, t, "loadError")} onRetry={() => refetch()} />
    );
  } else if (!data) {
    body = <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;
  } else if (data.length === 0) {
    body = <p className="text-sm text-muted-foreground">{t("reorder.empty")}</p>;
  } else {
    body = (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("hoard:common.name")}</TableHead>
            <TableHead>{t("reorder.kind")}</TableHead>
            <TableHead className="text-end">{t("hoard:common.onHand")}</TableHead>
            <TableHead className="text-end">{t("hoard:common.lowStockLimit")}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.map((row) => {
            const unit = row.unit ? ` ${row.unit}` : "";
            return (
              <TableRow key={`${row.kind}-${row.id}`}>
                <TableCell>
                  {row.kind === "material" ? (
                    <Link
                      to="/stock/materials/$materialId"
                      params={{ materialId: String(row.id) }}
                      className="hover:underline"
                    >
                      {row.name}
                    </Link>
                  ) : row.kind === "base" ? (
                    <Link
                      to="/stock/bases/$baseId"
                      params={{ baseId: String(row.id) }}
                      className="hover:underline"
                    >
                      {row.name}
                    </Link>
                  ) : (
                    <Link
                      to="/stock/products/$productId"
                      params={{ productId: String(row.id) }}
                      className="hover:underline"
                    >
                      {row.name}
                    </Link>
                  )}
                </TableCell>
                <TableCell>{t(`reorder.kinds.${row.kind}`)}</TableCell>
                <TableCell className="text-end tabular-nums">
                  {quantity(row.on_hand)}
                  {unit}
                </TableCell>
                <TableCell className="text-end tabular-nums">
                  {quantity(row.low_stock_limit)}
                  {unit}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    );
  }
  return (
    <Section title={t("reorder.title")} description={t("reorder.description")}>
      {body}
    </Section>
  );
}

function ValuationReport() {
  const { t } = useTranslation("reports");
  const quantity = useQuantity();
  const money = useMoney();
  const { data, error, isError, refetch } = useReadValuationReport();

  let body: ReactNode;
  if (isError) {
    body = (
      <StatusMessage description={errorMessage(error, t, "loadError")} onRetry={() => refetch()} />
    );
  } else if (!data) {
    body = <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;
  } else {
    body = (
      <div className="flex flex-col gap-4">
        <p>
          <span className="block text-sm text-muted-foreground">{t("valuation.total")}</span>
          <span className="text-3xl font-semibold tabular-nums">{money(data.total)}</span>
        </p>
        {data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("valuation.empty")}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("hoard:common.name")}</TableHead>
                <TableHead className="text-end">{t("hoard:common.onHand")}</TableHead>
                <TableHead className="text-end">{t("valuation.averageCost")}</TableHead>
                <TableHead className="text-end">{t("valuation.value")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.items.map((item) => {
                const attributes = Object.values(item.attributes).join(", ");
                return (
                  <TableRow key={item.item_id}>
                    <TableCell>
                      {item.name}
                      {attributes && (
                        <span className="ms-2 text-sm text-muted-foreground">{attributes}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-end tabular-nums">
                      {quantity(item.on_hand)}
                    </TableCell>
                    <TableCell className="text-end tabular-nums">
                      {money(item.average_cost, { exact: true })}
                    </TableCell>
                    <TableCell className="text-end tabular-nums">{money(item.value)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </div>
    );
  }
  return (
    <Section title={t("valuation.title")} description={t("valuation.description")}>
      {body}
    </Section>
  );
}

function ExportLinks() {
  const { t } = useTranslation("reports");
  return (
    <Section title={t("export.title")} description={t("export.description")}>
      <ul className="flex flex-col divide-y">
        {Object.values(ExportKind).map((kind) => (
          <li key={kind} className="flex flex-wrap items-center justify-between gap-2 py-2">
            <span>{t(`export.kinds.${kind}`)}</span>
            <span className="flex gap-2">
              {Object.values(ExportFormat).map((format) => (
                <Button key={format} asChild variant="outline" size="sm">
                  <a
                    href={`/api/v1/export/${kind}?format=${format}`}
                    download={`${kind}.${format}`}
                    aria-label={t("export.download", {
                      kind: t(`export.kinds.${kind}`),
                      format: t(`export.formats.${format}`),
                    })}
                  >
                    <Download aria-hidden />
                    {t(`export.formats.${format}`)}
                  </a>
                </Button>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </Section>
  );
}
