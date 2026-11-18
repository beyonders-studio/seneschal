import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type ImportChange,
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

/** Each importer: the files it takes, each a field of the upload; the one it
 *  needs, if any; whether it can bring stock on hand; and whether it changes
 *  what Hoard holds, rather than only making what is missing. */
const IMPORTERS = {
  stocksmith: {
    files: ["materials", "recipes", "manufactures"],
    needs: "materials",
    stock: true,
    changes: false,
  },
  spreadsheets: {
    files: ["materials", "bases", "products", "recipes"],
    needs: null,
    stock: false,
    changes: true,
  },
} as const;

export type Importer = keyof typeof IMPORTERS;

export function isImporter(id: string): id is Importer {
  return Object.hasOwn(IMPORTERS, id);
}

/** The namespaces a code a row was refused with may be translated in: the
 *  import's own, then those of the write paths it goes through. */
const CODE_NAMESPACES = [
  "import",
  "recipes",
  "catalog",
  "stores",
  "fields",
  "connection",
  "jobs",
  "hoard",
];

/** Where in a file something is: its file, its row and its column. */
type Place = { file?: string | null; row?: number | null; field?: string | null };

/** What an import's job did, by kind, and the rows it left out. */
type ImportResult = {
  created?: Record<string, number>;
  changed?: Record<string, number>;
  failed_rows?: (Place & { code: string })[];
};

/** Today, as a date field holds it. */
const today = () => new Date().toISOString().slice(0, 10);

/**
 * Settings › Import: an admin chooses an importer's files, checks them, and
 * imports them as a job whose progress the page follows. A check names what
 * the import would make and change, each change, and the problems that stop
 * it; changing a file or an option asks for a new check.
 */
export function ImportPage({ importer }: { importer: Importer }) {
  const { t } = useTranslation("import");
  const { admin } = useSession();
  const shape = IMPORTERS[importer];
  const [files, setFiles] = useState<Record<string, File>>({});
  const [bringStock, setBringStock] = useState(false);
  const [stockDated, setStockDated] = useState(today);
  const [checked, setChecked] = useState<ImportCheckRead | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const check = useCheckImport();
  const start = useStartImport();
  const { data: job } = useJob(jobId);

  const body = () => ({
    ...files,
    ...(shape.stock && {
      options: JSON.stringify({ stock_dated: bringStock ? stockDated : null }),
    }),
  });
  const changed = () => {
    setChecked(null);
    setJobId(null);
  };
  const running = job !== undefined && job.state !== JobState.done && job.state !== JobState.failed;
  const ready = checked !== null && checked.problems.length === 0;
  const chosen = shape.needs ? files[shape.needs] !== undefined : Object.keys(files).length > 0;
  const title = t(`importers.${importer}.title`);

  return (
    <HoardPage title={title}>
      <div className="flex max-w-3xl flex-col gap-6">
        <div className="flex flex-col gap-2">
          <Link
            to="/settings"
            className="flex items-center gap-1 text-sm text-muted-foreground hover:underline"
          >
            <ArrowLeft className="size-4" aria-hidden />
            {t("back")}
          </Link>
          <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
          <p className="text-sm text-muted-foreground">{t(`importers.${importer}.description`)}</p>
        </div>
        {!admin ? (
          <Alert>
            <AlertDescription>{t("adminOnly")}</AlertDescription>
          </Alert>
        ) : (
          <>
            <div className="grid grid-cols-fill-64/3 gap-4">
              {shape.files.map((name) => (
                <Field
                  key={name}
                  label={t(`importers.${importer}.files.${name}`)}
                  hint={
                    files[name]
                      ? t("files.chosen", { name: files[name]?.name })
                      : t(`importers.${importer}.files.${name}Hint`)
                  }
                >
                  <FileDropArea
                    accept=".csv,text/csv"
                    prompt={t("files.prompt")}
                    disabled={running}
                    onFile={(file) => {
                      setFiles((held) => ({ ...held, [name]: file }));
                      changed();
                    }}
                  />
                </Field>
              ))}
            </div>
            {shape.stock && (
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
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                variant={ready ? "outline" : "default"}
                disabled={!chosen || running || (bringStock && !stockDated)}
                pending={check.isPending}
                onClick={() =>
                  check.mutate(
                    { importerId: importer, data: body() },
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
                    { importerId: importer, data: body() },
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
            {job && <ImportProgress job={job} importer={importer} />}
            {checked && !job && <CheckFound checked={checked} importer={importer} />}
          </>
        )}
      </div>
    </HoardPage>
  );
}

/** Where a problem, a change or a refused row is: its file and row, and its
 *  column when it names one. */
function useWhere(importer: Importer) {
  const { t } = useTranslation("import");
  return ({ file, row, field }: Place) => {
    const named = file ? t(`importers.${importer}.files.${file}`, { defaultValue: file }) : null;
    if (!named || row == null) return named;
    if (!field) return t("where.row", { file: named, row });
    return t("where.column", {
      file: named,
      row,
      column: t(`columns.${field}`, { defaultValue: field }),
    });
  };
}

/** A code a check or a refused row names, from the first namespace that has it. */
function useCode() {
  const { t } = useTranslation(CODE_NAMESPACES);
  return (code: string, name?: string | null) =>
    t(
      [
        `import:problems.codes.${code}`,
        ...CODE_NAMESPACES.map((namespace) => `${namespace}:errors.${code}`),
        "import:problems.codes.unknown",
      ],
      { name: name ?? "" }
    );
}

/** What a check found: the counts by kind, each change, then the problems or
 *  that there are none. */
function CheckFound({ checked, importer }: { checked: ImportCheckRead; importer: Importer }) {
  const { t, i18n } = useTranslation("import");
  const number = numberFormat(i18n.resolvedLanguage);
  const changes = IMPORTERS[importer].changes;
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
              {changes && <TableHead className="text-end">{t("counts.changed")}</TableHead>}
              <TableHead className="text-end">{t("counts.unchanged")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {checked.counts.map((count) => (
              <TableRow key={count.kind}>
                <TableCell>
                  {t(`counts.kinds.${count.kind}`, { defaultValue: count.kind })}
                </TableCell>
                <TableCell className="text-end tabular-nums">{number.format(count.new)}</TableCell>
                {changes && (
                  <TableCell className="text-end tabular-nums">
                    {number.format(count.changed ?? 0)}
                  </TableCell>
                )}
                <TableCell className="text-end tabular-nums">
                  {number.format(count.unchanged)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <p className="text-xs text-muted-foreground">{t(`importers.${importer}.countsNote`)}</p>
      </section>
      {checked.changes.length > 0 && (
        <section aria-labelledby="import-changes" className="flex flex-col gap-2">
          <h3 id="import-changes" className="font-semibold">
            {t("changes.title")}
          </h3>
          <ul className="flex flex-col gap-1 text-sm">
            {checked.changes.map((change) => (
              <li key={`${change.file}-${change.row}-${change.field}`}>
                <Change change={change} importer={importer} />
              </li>
            ))}
          </ul>
        </section>
      )}
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
                <Problem problem={problem} importer={importer} />
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/** One field a check would change: "Lavender, Low-stock limit: empty → 500". */
function Change({ change, importer }: { change: ImportChange; importer: Importer }) {
  const { t } = useTranslation("import");
  // The change names its column itself.
  const where = useWhere(importer)({ file: change.file, row: change.row });
  return (
    <>
      {where && <span className="text-muted-foreground">{where}: </span>}
      {t("changes.change", {
        name: change.name,
        column: t(`columns.${change.field}`, { defaultValue: change.field }),
        was: change.was || t("changes.empty"),
        now: change.now || t("changes.empty"),
      })}
    </>
  );
}

function Problem({ problem, importer }: { problem: ImportProblem; importer: Importer }) {
  const where = useWhere(importer)(problem);
  const code = useCode();
  return (
    <>
      {where && <span className="font-medium">{where}: </span>}
      {code(problem.code, problem.name)}
    </>
  );
}

/** The import's job: its progress, what it made and changed once done, and
 *  the rows it left out; or why it failed. */
function ImportProgress({ job, importer }: { job: JobRead; importer: Importer }) {
  const { t, i18n } = useTranslation(CODE_NAMESPACES);
  const where = useWhere(importer);
  const code = useCode();
  const number = numberFormat(i18n.resolvedLanguage);
  if (job.state === JobState.failed) {
    const keys = job.error ? CODE_NAMESPACES.map((ns) => `${ns}:errors.${job.error}`) : [];
    return (
      <p role="alert" className="text-sm text-destructive">
        {t([...keys, "jobs:failed"])}
      </p>
    );
  }
  if (job.state !== JobState.done) return <JobProgress job={job} />;
  const result = (job.result ?? {}) as ImportResult;
  const counted = [
    ...Object.entries(result.created ?? {}).map(([kind, count]) => ({ kind, count, made: true })),
    ...Object.entries(result.changed ?? {}).map(([kind, count]) => ({ kind, count, made: false })),
  ];
  const left = result.failed_rows ?? [];
  return (
    <section aria-labelledby="import-done" className="flex flex-col gap-2">
      <h3 id="import-done" className="font-semibold">
        {t("import:done.title")}
      </h3>
      {counted.length === 0 ? (
        <p className="text-sm">{t("import:done.nothing")}</p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm">
          {counted.map(({ kind, count, made }) => (
            <li key={`${kind}-${made}`}>
              {t(made ? "import:done.made" : "import:done.changed", {
                kind: t(`import:counts.kinds.${kind}`, { defaultValue: kind }),
                count: number.format(count),
              })}
            </li>
          ))}
        </ul>
      )}
      {left.length > 0 && (
        <>
          <h4 className="font-semibold text-destructive">{t("import:done.leftOut")}</h4>
          <ul className="flex flex-col gap-1 text-sm">
            {left.map((failure) => (
              <li key={`${failure.file}-${failure.row}`}>
                <span className="font-medium">{where(failure)}: </span>
                {code(failure.code)}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
