import { useTranslation } from "react-i18next";

import { type JobRead, JobState } from "@/api/generated/hoard.schemas";
import { numberFormat } from "@/ui/lib/intl";
import { Progress } from "@/ui/progress";

/** How far a job got: waiting to start, the step it is at and how much of it
 *  is done, done, or why it failed. A step and a failure are codes, read as
 *  `steps.<step>` and `errors.<code>` in the `jobs` namespace (a failure then
 *  among Hoard's common codes). */
export function JobProgress({ job }: { job: JobRead }) {
  const { t, i18n } = useTranslation("jobs");
  if (job.state === JobState.failed) {
    const keys = job.error ? [`errors.${job.error}`, `hoard:errors.${job.error}`] : [];
    return (
      <p role="alert" className="text-sm text-destructive">
        {t([...keys, "failed"])}
      </p>
    );
  }
  if (job.state === JobState.done) {
    return <p className="text-sm text-muted-foreground">{t("done")}</p>;
  }
  const step =
    job.state === JobState.queued
      ? t("queued")
      : t([...(job.step ? [`steps.${job.step}`] : []), "running"]);
  const counted = job.done !== null && job.total !== null && job.total > 0;
  const number = numberFormat(i18n.resolvedLanguage);
  return (
    <div role="status" className="flex min-w-40 flex-col gap-1">
      <span className="text-sm text-muted-foreground">
        {counted
          ? t("count", {
              step,
              done: number.format(job.done ?? 0),
              total: number.format(job.total ?? 0),
            })
          : step}
      </span>
      {counted && <Progress value={job.done} max={job.total ?? undefined} aria-label={step} />}
    </div>
  );
}
