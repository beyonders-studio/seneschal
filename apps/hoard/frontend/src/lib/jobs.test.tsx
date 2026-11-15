import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { JOB_POLL_MS, useJob } from "@/lib/jobs";
import { type Answer, answerApi, buildJob } from "@/testing";

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {children}
  </QueryClientProvider>
);

describe("useJob", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("reads the job again until it is done, then stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const answers: Record<string, Answer> = {
      "GET /api/v1/jobs/5": { status: 200, data: buildJob({ state: "running", step: "reading" }) },
    };
    const asked = answerApi(answers);

    const { result } = renderHook(() => useJob(5), { wrapper });
    await waitFor(() => expect(result.current.data?.state).toBe("running"));

    answers["GET /api/v1/jobs/5"] = {
      status: 200,
      data: buildJob({ state: "done", file_name: "lots.csv" }),
    };
    await vi.advanceTimersByTimeAsync(JOB_POLL_MS);
    await waitFor(() => expect(result.current.data?.state).toBe("done"));

    const readings = asked.length;
    await vi.advanceTimersByTimeAsync(JOB_POLL_MS * 3);
    expect(asked).toHaveLength(readings);
  });

  it("reads nothing without a job", async () => {
    const asked = answerApi({});
    const { result } = renderHook(() => useJob(null), { wrapper });
    expect(result.current.fetchStatus).toBe("idle");
    expect(asked).toHaveLength(0);
  });
});
