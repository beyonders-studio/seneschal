import { type JobRead, JobState } from "@/api/generated/hoard.schemas";
import { useReadJob } from "@/api/generated/jobs/jobs";

/** How often a job still waiting or running is read again. */
export const JOB_POLL_MS = 1000;

/** Whether a job has finished: done, or failed. */
export const jobFinished = (job: JobRead | undefined) =>
  job?.state === JobState.done || job?.state === JobState.failed;

/** Where the member who started a job downloads its file. */
export const jobResultUrl = (id: number) => `/api/v1/jobs/${id}/result`;

/** The job `id` names, read again every {@link JOB_POLL_MS} until it is done
 *  or failed; nothing for null. */
export function useJob(id: number | null) {
  return useReadJob(id ?? 0, {
    query: {
      enabled: id !== null,
      refetchInterval: (query) => (jobFinished(query.state.data) ? false : JOB_POLL_MS),
    },
  });
}
