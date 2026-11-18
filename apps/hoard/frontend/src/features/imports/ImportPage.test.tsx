import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ImportCheckRead } from "@/api/generated/hoard.schemas";
import { type Answer, buildJob, openSignedIn, signedIn } from "@/testing";

const ADMIN = signedIn({ admin: true, initiative_id: null, page: "settings" });

const CHECKED: ImportCheckRead = {
  counts: [
    { kind: "materials", new: 2, changed: 0, unchanged: 1 },
    { kind: "stock", new: 2, changed: 0, unchanged: 0 },
  ],
  changes: [],
  problems: [],
};

/** Picks `content` as the file the `index`th file field takes. */
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
          result: {
            created: { materials: 2 },
            matched: {},
            changed: {},
            failed: {},
            failed_rows: [],
          },
        }),
      },
    };
    const asked = await openSignedIn("/settings/import/stocksmith", ADMIN, answers);
    await screen.findByRole("heading", { level: 2, name: "Import from Stocksmith" });
    const start = screen.getByRole<HTMLButtonElement>("button", { name: "Import" });
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Check" }).disabled).toBe(true);

    choose(0, "materials.csv");
    fireEvent.click(screen.getByRole("switch", { name: "Bring stock on hand, dated" }));
    fireEvent.change(screen.getByLabelText("Dated"), { target: { value: "2026-12-31" } });
    fireEvent.click(screen.getByRole("button", { name: "Check" }));

    await screen.findByText("Starting stock");
    expect(screen.getByText("No problems found: the files are ready to import.")).toBeTruthy();
    // Stocksmith's import only makes what is missing, so nothing counts as changed.
    expect(screen.queryByRole("columnheader", { name: "Changed" })).toBeNull();
    const sent = asked.find((config) => config.url === "/api/v1/imports/stocksmith/check")
      ?.data as FormData;
    expect((sent.get("materials") as File).name).toBe("materials.csv");
    expect(sent.get("recipes")).toBeNull();
    expect(sent.get("options")).toBe(JSON.stringify({ stock_dated: "2026-12-31" }));

    await waitFor(() => expect(start.disabled).toBe(false));
    fireEvent.click(start);
    await screen.findByText("Materials: 2 new");
    expect(asked.some((config) => config.url === "/api/v1/imports/stocksmith")).toBe(true);
  });

  it("lists the problems a check finds, and imports nothing", async () => {
    await openSignedIn("/settings/import/stocksmith", ADMIN, {
      "POST /api/v1/imports/stocksmith/check": {
        status: 200,
        data: {
          counts: [],
          changes: [],
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
      "“Ghost Leaf” isn't a material, base or product in the files or in Hoard."
    );
    expect(screen.getByText("Recipes export, row 12:")).toBeTruthy();
    expect(
      screen.getByText("This isn't the file it was chosen as: it has no “SKU” column.")
    ).toBeTruthy();
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Import" }).disabled).toBe(true);
  });

  it("says why a check was refused", async () => {
    await openSignedIn("/settings/import/stocksmith", ADMIN, {
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
    await openSignedIn("/settings/import/stocksmith", { ...ADMIN, admin: false });
    await screen.findByText("Only the community's admins can import.");
    expect(screen.queryByRole("button", { name: "Check" })).toBeNull();
  });
});

describe("Import Hoard spreadsheets", () => {
  it("lists each change a check finds, then the rows the import left out", async () => {
    const asked = await openSignedIn("/settings/import/spreadsheets", ADMIN, {
      "POST /api/v1/imports/spreadsheets/check": {
        status: 200,
        data: {
          counts: [{ kind: "materials", new: 0, changed: 1, unchanged: 3 }],
          changes: [
            {
              file: "materials",
              row: 4,
              name: "Lavender",
              field: "low_stock_limit",
              was: "",
              now: "500",
            },
          ],
          problems: [],
        },
      },
      "POST /api/v1/imports/spreadsheets": { status: 202, data: buildJob({ id: 6 }) },
      "GET /api/v1/jobs/6": {
        status: 200,
        data: buildJob({
          id: 6,
          kind: "import",
          state: "done",
          result: {
            created: {},
            matched: { materials: 3 },
            changed: { materials: 1 },
            failed: { products: 1 },
            failed_rows: [{ file: "products", row: 2, field: null, code: "STORE_UNREACHABLE" }],
          },
        }),
      },
    });
    await screen.findByRole("heading", { level: 2, name: "Import Hoard spreadsheets" });
    expect(screen.queryByRole("switch")).toBeNull();

    // Any one of the files is enough.
    choose(2, "products.csv");
    fireEvent.click(screen.getByRole("button", { name: "Check" }));

    await screen.findByText("Lavender, Low-stock limit: empty → 500");
    expect(screen.getByText("Materials, row 4:")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Changed" })).toBeTruthy();
    const sent = asked.find((config) => config.url === "/api/v1/imports/spreadsheets/check")
      ?.data as FormData;
    expect((sent.get("products") as File).name).toBe("products.csv");
    expect(sent.get("options")).toBeNull();

    const start = screen.getByRole<HTMLButtonElement>("button", { name: "Import" });
    await waitFor(() => expect(start.disabled).toBe(false));
    fireEvent.click(start);

    await screen.findByText("Materials: 1 changed");
    expect(screen.getByText("Left out")).toBeTruthy();
    expect(screen.getByText("Products, row 2:")).toBeTruthy();
    expect(screen.getByText("The store couldn't be reached. Try again in a moment.")).toBeTruthy();
  });

  it("names the column a problem is in", async () => {
    await openSignedIn("/settings/import/spreadsheets", ADMIN, {
      "POST /api/v1/imports/spreadsheets/check": {
        status: 200,
        data: {
          counts: [],
          changes: [],
          problems: [
            {
              code: "NUMBER_INVALID",
              file: "materials",
              row: 3,
              field: "low_stock_limit",
              name: "1,000",
            },
          ],
        },
      },
    });
    await screen.findByRole("heading", { level: 2, name: "Import Hoard spreadsheets" });
    choose(0, "materials.csv");
    fireEvent.click(screen.getByRole("button", { name: "Check" }));

    await screen.findByText("“1,000” isn't a number. Write it without thousands separators.");
    expect(screen.getByText("Materials, row 3, Low-stock limit:")).toBeTruthy();
  });
});
