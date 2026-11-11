import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import "@/i18n";
import { apiClient } from "@/api/mutator";
import { useMemberNames } from "@/lib/members";
import { answerApi } from "@/testing";

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {children}
  </QueryClientProvider>
);

describe("useMemberNames", () => {
  it("names each reference once, a former member, and Hoard itself", async () => {
    const asked = answerApi({
      "GET /api/v1/members": {
        status: 200,
        data: [
          { member_ref: "ref-ada", name: "Ada" },
          { member_ref: "ref-gone", name: null },
        ],
      },
    });

    const { result } = renderHook(() => useMemberNames(["ref-gone", "ref-ada", null, "ref-ada"]), {
      wrapper,
    });

    await waitFor(() => expect(result.current("ref-ada")).toBe("Ada"));
    expect(result.current("ref-gone")).toBe("Former member");
    expect(result.current(null)).toBe("Hoard");
    expect(asked).toHaveLength(1);
    expect(apiClient.getUri(asked[0])).toBe(
      "/api/v1/members?member_ref=ref-ada&member_ref=ref-gone"
    );
  });

  it("says only that it was a member when Initiative can't be asked", async () => {
    answerApi({
      "GET /api/v1/members": { status: 503, data: { detail: "INITIATIVE_UNAVAILABLE" } },
    });

    const { result } = renderHook(() => useMemberNames(["ref-ada"]), { wrapper });

    await waitFor(() => expect(result.current("ref-ada")).toBe("A member"));
  });
});
