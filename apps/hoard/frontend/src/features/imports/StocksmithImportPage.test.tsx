import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ImportCheckRead } from "@/api/generated/hoard.schemas";
import { type Answer, buildJob, openSignedIn, signedIn } from "@/testing";

const ADMIN = signedIn({ admin: true, initiative_id: null, page: "settings" });

const CHECKED: ImportCheckRead = {
  counts: [
    { kind: "materials", new: 2, existing: 1 },
    { kind: "stock", new: 2, existing: 0 },
  ],
  problems: [],
};

/** Picks `content` as the export the `index`th file field takes. */
function choose(index: number, name: string, content = "name\n") {
  const inputs = document.querySelectorAll<HTMLInputElement>('input[type="file"]');
  fireEvent.change(inputs[index] as HTMLInputElement, {
    target: { files: [new File([content], name, { type: "text/csv" })] },
  });
}

describe("Import from Stocksmith", () => {
  it("checks the exports, then imports them and follows the job", async () => {
    const answers: Record<string, Answer> = {
      "POST /api/v1/imports/stocksmith/check": { status: 200, data: CHECKED },
      "POST /api/v1/imports/stocksmith": {
        status: 202,
        data: buildJob({ id: 5, kind: "import" }),
      },
      "GET /api/v1/jobs/5": {
        status: 200,
        data: buildJob({
          id: 5,
          kind: "import",
          state: "done",
          result: { made: { materials: 2 } },
        }),
      },
    };
    const asked = await openSignedIn("/settings/import", ADMIN, answers);
    await screen.findByRole("heading", { level: 2, name: "Import from Stocksmith" });
    const start = screen.getByRole<HTMLButtonElement>("button", { name: "Import" });
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Check" }).disabled).toBe(true);

    choose(0, "materials.csv");
    fireEvent.click(screen.getByRole("switch", { name: "Bring stock on hand, dated" }));
    fireEvent.change(screen.getByLabelText("Dated"), { target: { value: "2026-12-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Check" }));

    await screen.findByText("Starting stock");
    expect(screen.getByText("No problems found: the files are ready to import.")).toBeTruthy();
    const sent = asked.find((config) => config.url === "/api/v1/imports/stocksmith/check")
      ?.data as FormData;
    expect((sent.get("materials") as File).name).toBe("materials.csv");
    expect(sent.get("recipes")).toBeNull();
    expect(sent.get("options")).toBe(JSON.stringify({ stock_dated: "2026-12-31" }));

    await waitFor(() => expect(start.disabled).toBe(false));
    fireEvent.click(start);
    await screen.findByText("Materials: 2");
    expect(asked.some((config) => config.url === "/api/v1/imports/stocksmith")).toBe(true);
  });

  it("lists the problems a check finds, and imports nothing", async () => {
    await openSignedIn("/settings/import", ADMIN, {
      "POST /api/v1/imports/stocksmith/check": {
        status: 200,
        data: {
          counts: [],
          problems: [
            { code: "LINE_NAMES_NOTHING", file: "recipes", row: 12, name: "Ghost Leaf" },
            { code: "COLUMN_MISSING", file: "manufactures", row: 1, name: "SKU" },
          ],
        },
      },
    });
    await screen.findByRole("heading", { level: 2, name: "Import from Stocksmith" });

    choose(0, "materials.csv");
    choose(1, "recipes.csv");
    fireEvent.click(screen.getByRole("button", { name: "Check" }));

    await screen.findByText(
      "“Ghost Leaf” isn't a material, base or product in either file or in Hoard."
    );
    expect(screen.getByText("Recipes export, row 12:")).toBeTruthy();
    expect(
      screen.getByText("This isn't the export it was chosen as: it has no “SKU” column.")
    ).toBeTruthy();
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Import" }).disabled).toBe(true);
  });

  it("says why a check was refused", async () => {
    await openSignedIn("/settings/import", ADMIN, {
      "POST /api/v1/imports/stocksmith/check": {
        status: 413,
        data: { detail: "IMPORT_FILE_TOO_LARGE" },
      },
    });
    await screen.findByRole("heading", { level: 2, name: "Import from Stocksmith" });
    choose(0, "materials.csv");
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    await screen.findByText("That file is too large to import.");
  });

  it("is for the community's admins", async () => {
    await openSignedIn("/settings/import", { ...ADMIN, admin: false });
    await screen.findByText("Only the community's admins can import.");
    expect(screen.queryByRole("button", { name: "Check" })).toBeNull();
  });
});
