import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { type Answer, openSignedIn, signedIn } from "@/testing";

const settings = (regimes: string[]): Answer => ({
  status: 200,
  data: { currency: "USD", auto_manufacture: "off", regimes },
});

describe("BackupSection", () => {
  it("shows a member the rules ticked and how long they ask records kept", async () => {
    await openSignedIn("/settings", signedIn({ page: "settings" }), {
      "GET /api/v1/settings": settings(["eu_cosmetics", "us_organic"]),
      "GET /api/v1/documents/backup-period": { status: 200, data: { months: 120 } },
    });

    await screen.findByText("Together they ask for records to be kept for 10 years.");
    screen.getByText("European Union and United Kingdom: cosmetics");
    screen.getByText("10 years after a product's last batch");
    screen.getByText("United States: organic certification");
    expect(screen.queryByText("Canada: food traceability")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("lets an admin tick the rules, and reads the period again", async () => {
    const asked = await openSignedIn("/settings", signedIn({ page: "settings", admin: true }), {
      "GET /api/v1/settings": settings(["eu_food"]),
      "PUT /api/v1/settings": settings(["eu_food", "canada_food"]),
      "GET /api/v1/documents/backup-period": { status: 200, data: { months: 6 } },
    });

    await screen.findByText("Together they ask for records to be kept for 6 months.");
    fireEvent.click(screen.getByRole("checkbox", { name: "Canada: food traceability" }));

    await waitFor(() => {
      const put = asked.find((each) => each.method === "put");
      expect(JSON.parse(String(put?.data))).toEqual({ regimes: ["eu_food", "canada_food"] });
    });
    await waitFor(() =>
      expect(asked.filter((each) => each.url === "/api/v1/documents/backup-period")).toHaveLength(2)
    );
  });

  it("asks for a rule to be ticked while none is", async () => {
    await openSignedIn("/settings", signedIn({ page: "settings", admin: true }), {
      "GET /api/v1/settings": settings([]),
      "GET /api/v1/documents/backup-period": { status: 200, data: { months: null } },
    });

    await screen.findByText(/Tick the rules you keep records for/);
    expect(screen.getAllByRole("checkbox")).toHaveLength(7);
  });
});
