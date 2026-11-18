import { fireEvent, screen, waitFor } from "@testing-library/react";
import { AxiosError, type AxiosResponse } from "axios";
import { describe, expect, it } from "vitest";

import { apiClient } from "@/api/mutator";
import { browser } from "@/lib/browser";
import { answerApi, openPageAt, signedIn } from "@/testing";

const ME = "GET /api/v1/auth/me";
const REFRESH = "POST /api/v1/auth/refresh";

/** Opens Hoard at its own address, at `path`. */
async function openStandalone(path: string) {
  browser.framed = () => false;
  window.history.replaceState(null, "", path);
  await openPageAt(path);
}

describe("Hoard at its own address", () => {
  it("opens on the sign-in the reader already holds, with its own bar", async () => {
    answerApi({ [ME]: { status: 200, data: signedIn() } });
    await openStandalone("/stock");

    await screen.findByRole("heading", { name: "Stock" });
    expect(screen.getByRole("navigation", { name: "Hoard's pages" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
    expect(browser.go).not.toHaveBeenCalled();
  });

  it("renews a session that ended, and asks for no handoff", async () => {
    const asked = answerApi({
      [ME]: { status: 401 },
      [REFRESH]: { status: 200, data: signedIn() },
    });
    await openStandalone("/stock");

    await screen.findByRole("heading", { name: "Stock" });
    const calls = asked.map((request) => `${request.method?.toUpperCase()} ${request.url}`);
    expect(calls.slice(0, 2)).toEqual([ME, REFRESH]);
    expect(calls).not.toContain("POST /api/v1/auth/handoff/exchange");
  });

  it("sends the reader to sign in with Initiative, and back to this page", async () => {
    answerApi({ [ME]: { status: 401 }, [REFRESH]: { status: 401 } });
    await openStandalone("/production?tab=runs");

    await waitFor(() =>
      expect(browser.go).toHaveBeenCalledWith(
        "/api/v1/auth/sign-in/start?next=%2Fproduction%3Ftab%3Druns"
      )
    );
  });

  it("says why the reader couldn't sign in, and offers to try again", async () => {
    answerApi({});
    await openStandalone("/?sign_in_error=SIGN_IN_CANCELLED");

    await screen.findByText("You didn't finish signing in with Initiative.");
    expect(window.location.search).toBe("");
    fireEvent.click(screen.getByRole("button", { name: "Sign in with Initiative" }));
    expect(browser.go).toHaveBeenCalledWith("/api/v1/auth/sign-in/start?next=%2F");
  });

  it("renews once when a call finds the session ended, and makes the call again", async () => {
    const asked = answerApi({
      [ME]: { status: 200, data: signedIn() },
      [REFRESH]: { status: 200, data: signedIn() },
    });
    await openStandalone("/stock");
    await screen.findByRole("heading", { name: "Stock" });
    const answer = apiClient.defaults.adapter as (config: never) => Promise<AxiosResponse>;
    let probes = 0;
    apiClient.defaults.adapter = async (config) => {
      if (config.url !== "/probe") return answer(config as never);
      asked.push(config);
      probes += 1;
      if (probes > 1) return { data: "again", status: 200, statusText: "", headers: {}, config };
      throw new AxiosError("ended", undefined, config, null, {
        data: { detail: "NOT_AUTHENTICATED" },
        status: 401,
        statusText: "",
        headers: {},
        config,
      });
    };

    const { data } = await apiClient.get("/probe");

    expect(data).toBe("again");
    const calls = asked.map((request) => `${request.method?.toUpperCase()} ${request.url}`);
    expect(calls.filter((call) => call === "GET /probe")).toHaveLength(2);
    expect(calls).toContain(ME);
  });
});
