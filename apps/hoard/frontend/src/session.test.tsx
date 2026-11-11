import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import i18n from "@/i18n";
import { answerApi, openPage, post, sendHandoff, signedIn } from "@/testing";
import { getClock } from "@/ui/host";

describe("the session", () => {
  it("starts from Initiative's handoff and follows the reader after", async () => {
    const asked = answerApi({
      "POST /api/v1/auth/handoff/exchange": { status: 200, data: signedIn() },
      "GET /api/v1/auth/me": { status: 200, data: signedIn() },
    });
    const parent = vi.spyOn(window, "postMessage");
    await openPage("/stock");
    expect(parent).toHaveBeenCalledWith({ type: "initiative-plugin:ready" }, "*");

    sendHandoff({
      handoff_token: "minted",
      locale: "de",
      theme: "dark",
      theme_colors: {
        "--primary": "oklch(0.6 0.2 250)",
        "--radius": "0",
        "--card": "url(//elsewhere/x)",
      },
      time_format: "24",
      week_start: 1,
    });

    await screen.findByRole("heading", { name: "Stock" });
    expect(asked.map((request) => `${request.method?.toUpperCase()} ${request.url}`)).toEqual([
      "POST /api/v1/auth/handoff/exchange",
      "GET /api/v1/auth/me",
    ]);
    expect(JSON.parse(asked[0].data)).toEqual({
      handoff_token: "minted",
      theme: "dark",
      locale: "de",
    });

    // The reader's language, appearance and clock, from the handoff.
    const root = document.documentElement;
    expect(i18n.resolvedLanguage).toBe("de");
    expect(root.classList.contains("dark")).toBe(true);
    expect(root.style.getPropertyValue("--primary")).toBe("oklch(0.6 0.2 250)");
    expect(root.style.getPropertyValue("--radius")).toBe("");
    expect(root.style.getPropertyValue("--card")).toBe("");
    expect(getClock()).toEqual({ timeFormat: "24", weekStart: 1 });

    // And as the reader changes them in Initiative.
    post({ type: "initiative-plugin:theme", theme: "light" });
    post({ type: "initiative-plugin:locale", locale: "fr" });
    post({ type: "initiative-plugin:clock", time_format: "12", week_start: 0 });
    expect(root.classList.contains("dark")).toBe(false);
    expect(root.style.getPropertyValue("--primary")).toBe("");
    await waitFor(() => expect(i18n.resolvedLanguage).toBe("fr"));
    expect(getClock()).toEqual({ timeFormat: "12", weekStart: 0 });
  });

  it("says it couldn't open, here and to Initiative, when the handoff is refused", async () => {
    answerApi({ "POST /api/v1/auth/handoff/exchange": { status: 401 } });
    const parent = vi.spyOn(window, "postMessage");
    await openPage("/stock");
    sendHandoff();

    await screen.findByText("Hoard couldn't open");
    expect(screen.queryByRole("heading", { name: "Stock" })).toBeNull();
    expect(parent).toHaveBeenCalledWith(
      { type: "initiative-plugin:error", message: "Try opening Hoard again in a moment." },
      "*"
    );
  });
});
