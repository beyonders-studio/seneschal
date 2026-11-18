import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type ImportCheckRead,
  type ImportProblem,
  type JobRead,
  JobState,
} from "@/api/generated/hoard.schemas";
import { useCheckImport, useStartImport } from "@/api/generated/imports/imports";
import { HoardPage } from "@/components/HoardPage";
import { JobProgress } from "@/components/JobProgress";
import { errorMessage } from "@/lib/errors";
import { useJob } from "@/lib/jobs";
import { useSession } from "@/session";
import { Alert, AlertDescription } from "@/ui/alert";
import { Button } from "@/ui/button";
import { Field } from "@/ui/field";
import { FileDropArea } from "@/ui/file-drop";
import { Input } from "@/ui/input";
import { numberFormat } from "@/ui/lib/intl";
import { Switch } from "@/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** The importer this page uses. */
const IMPORTER = "stocksmith";
/** The exports it takes, each a field of the upload; the first is required. */
const FILES = ["materials", "recipes", "manufactures"] as const;
type FileName = (typeof FILES)[number];

/** The namespaces a failed import's code may be translated in: the codes of
 *  the write paths the import goes through. */
const FAILURE_NAMESPACES = ["import", "recipes", "catalog", "connection", "jobs", "hoard"];

/** Today, as a date field holds it. */
const today = () => new Date().toISOString().slice(0, 10);

/**
 * Settings › Import: an admin chooses Stocksmith's exports, checks them, and
 * imports them as a job whose progress the page follows. A check names what
 * the import would make and the problems that stop it; changing a file or
 * the stock switch asks for a new check.
 */
export function StocksmithImportPage() {
  const { t } = useTranslation("import");
  const { admin } = useSession();
  const [files, setFiles] = useState<Partial<Record<FileName, File>>>({});
  const [bringStock, setBringStock] = useState(false);
  const [stockDated, setStockDated] = useState(today);
  const [checked, setChecked] = useState<ImportCheckRead | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const check = useCheckImport();
  const start = useStartImport();
  const { data: job } = useJob(jobId);

  const body = () => ({
    ...files,
    options: JSON.stringify({ stock_dated: bringStock ? stockDated : null }),
  });
  const changed = () => {
    setChecked(null);
    setJobId(null);
  };
  const running = job !== undefined && job.state !== JobState.done && job.state !== JobState.failed;
  const ready = checked !== null && checked.problems.length === 0;

  return (
    <HoardPage title={t("title")}>
      <div className="flex max-w-3xl flex-col gap-6">
        <div className="flex flex-col gap-2">
          <Link
            to="/settings"
            className="flex items-center gap-1 text-sm text-muted-foreground hover:underline"
          >
            <ArrowLeft className="size-4" aria-hidden />
            {t("back")}
          </Link>
          <h2 className="text-lg font-semibold tracking-tight">{t("title")}</h2>
          <p className="text-sm text-muted-foreground">{t("description")}</p>
        </div>
        {!admin ? (
          <Alert>
            <AlertDescription>{t("adminOnly")}</AlertDescription>
          </Alert>
        ) : (
          <>
            <div className="grid grid-cols-fill-64/3 gap-4">
              {FILES.map((name) => (
                <Field
                  key={name}
                  label={t(`files.${name}`)}
                  hint={
                    files[name]
                      ? t("files.chosen", { name: files[name]?.name })
                      : t(`files.${name}Hint`)
                  }
                >
                  <FileDropArea
                    accept=".csv,text/csv"
                    prompt={t("files.prompt")}
                    disabled={running}
                    onFile={(file) => {
                      setFiles((chosen) => ({ ...chosen, [name]: file }));
                      changed();
                    }}
                  />
                </Field>
              ))}
            </div>
            <div className="flex flex-wrap items-end gap-4">
              <Field label={t("stock.label")} hint={t("stock.hint")} className="max-w-md">
                <Switch
                  checked={bringStock}
                  disabled={running}
                  onCheckedChange={(on) => {
                    setBringStock(on);
                    changed();
                  }}
                />
              </Field>
              {bringStock && (
                <Field label={t("stock.date")}>
                  <Input
                    type="date"
                    required
                    value={stockDated}
                    disabled={running}
                    onChange={(event) => {
                      setStockDated(event.target.value);
                      changed();
                    }}
                  />
                </Field>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant={ready ? "outline" : "default"}
                disabled={!files.materials || running || (bringStock && !stockDated)}
                pending={check.isPending}
                onClick={() =>
                  check.mutate(
                    { importerId: IMPORTER, data: body() },
                    {
                      onSuccess: (found) => {
                        setChecked(found);
                        setJobId(null);
                      },
                      onError: () => setChecked(null),
                    }
                  )
                }
              >
                {t("check")}
              </Button>
              <Button
                disabled={!ready || running || jobId !== null}
                pending={start.isPending}
                onClick={() =>
                  start.mutate(
                    { importerId: IMPORTER, data: body() },
                    { onSuccess: (queued) => setJobId(queued.id) }
                  )
                }
              >
                {t("start")}
              </Button>
            </div>
            {check.isError && (
              <p role="alert" className="text-sm text-destructive">
                {errorMessage(check.error, t, "checkFailed")}
              </p>
            )}
            {start.isError && (
              <p role="alert" className="text-sm text-destructive">
                {errorMessage(start.error, t, "startFailed")}
              </p>
            )}
            {job && <ImportProgress job={job} />}
            {checked && !job && <CheckFound checked={checked} />}
          </>
        )}
      </div>
    </HoardPage>
  );
}

/** What a check found: the counts by kind, then the problems or that there are none. */
function CheckFound({ checked }: { checked: ImportCheckRead }) {
  const { t, i18n } = useTranslation("import");
  const number = numberFormat(i18n.resolvedLanguage);
  return (
    <div className="flex flex-col gap-4">
      <section aria-labelledby="import-counts" className="flex flex-col gap-2">
        <h3 id="import-counts" className="font-semibold">
          {t("counts.title")}
        </h3>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("counts.kind")}</TableHead>
              <TableHead className="text-end">{t("counts.new")}</TableHead>
              <TableHead className="text-end">{t("counts.existing")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {checked.counts.map((count) => (
              <TableRow key={count.kind}>
                <TableCell>
                  {t(`counts.kinds.${count.kind}`, { defaultValue: count.kind })}
                </TableCell>
                <TableCell className="text-end tabular-nums">{number.format(count.new)}</TableCell>
                <TableCell className="text-end tabular-nums">
                  {number.format(count.existing)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <p className="text-xs text-muted-foreground">{t("counts.note")}</p>
      </section>
      {checked.problems.length === 0 ? (
        <p role="status" className="text-sm">
          {t("problems.none")}
        </p>
      ) : (
        <section aria-labelledby="import-problems" className="flex flex-col gap-2">
          <h3 id="import-problems" className="font-semibold text-destructive">
            {t("problems.title")}
          </h3>
          <ul className="flex flex-col gap-1 text-sm">
            {checked.problems.map((problem, index) => (
              // Problems have no id of their own, and a check's list never changes.
              // biome-ignore lint/suspicious/noArrayIndexKey: see above
              <li key={index}>
                <Problem problem={problem} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Problem({ problem }: { problem: ImportProblem }) {
  const { t } = useTranslation("import");
  const file = problem.file ? t(`files.${problem.file}`, { defaultValue: problem.file }) : null;
  const where =
    file && problem.row != null ? t("problems.where", { file, row: problem.row }) : file;
  return (
    <>
      {where && <span className="font-medium">{where}: </span>}
      {t([`problems.codes.${problem.code}`, "problems.codes.unknown"], {
        name: problem.name ?? "",
      })}
    </>
  );
}

/** The import's job: its progress, what it made once done, or why it failed. */
function ImportProgress({ job }: { job: JobRead }) {
  const { t, i18n } = useTranslation(FAILURE_NAMESPACES);
  const number = numberFormat(i18n.resolvedLanguage);
  if (job.state === JobState.failed) {
    const keys = job.error ? FAILURE_NAMESPACES.map((ns) => `${ns}:errors.${job.error}`) : [];
    return (
      <p role="alert" className="text-sm text-destructive">
        {t([...keys, "jobs:failed"])}
      </p>
    );
  }
  if (job.state !== JobState.done) return <JobProgress job={job} />;
  const made = Object.entries((job.result as { made?: Record<string, number> } | null)?.made ?? {});
  return (
    <section aria-labelledby="import-made" className="flex flex-col gap-2">
      <h3 id="import-made" className="font-semibold">
        {t("import:made.title")}
      </h3>
      {made.length === 0 ? (
        <p className="text-sm">{t("import:made.nothing")}</p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm">
          {made.map(([kind, count]) => (
            <li key={kind}>
              {t("import:made.count", {
                kind: t(`import:counts.kinds.${kind}`, { defaultValue: kind }),
                count: number.format(count),
              })}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
