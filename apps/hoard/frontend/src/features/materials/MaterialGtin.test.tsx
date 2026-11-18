import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { materialDetail, SETTINGS } from "@/features/items/testing";
import { openSignedIn, signedIn } from "@/testing";

// The type picker scrolls the option it marks into view, which jsdom can't.
Element.prototype.scrollIntoView ??= () => {};

const GTIN = "00012345678905";

const opened = (gtin: string | null, layouts: unknown[] = []) => {
  const material = materialDetail({ gtin });
  return {
    ...SETTINGS,
    "GET /api/v1/materials/5": { status: 200, data: material },
    "PUT /api/v1/materials/5": { status: 200, data: material },
    "GET /api/v1/layouts/material": { status: 200, data: { layouts } },
  };
};

/** The material's type's page, changed to show its SKU and nothing else. */
const WITHOUT_GTIN = {
  kind: "material",
  type_id: 30,
  definition: { main: [{ type: "field", props: { field: "sku" } }] },
  updated_at: "2026-11-18T10:00:00Z",
};

const editDialog = async () => {
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  return screen.findByRole("dialog", { name: "Edit material" });
};

const saved = async (asked: { method?: string; url?: string; data?: string }[]) => {
  await waitFor(() =>
    expect(asked.some((each) => each.method === "put" && each.url === "/api/v1/materials/5")).toBe(
      true
    )
  );
  const put = asked.find((each) => each.method === "put" && each.url === "/api/v1/materials/5");
  return JSON.parse(put?.data ?? "{}");
};

describe("A material's GTIN", () => {
  it("shows on its page and is asked for where its layout places it", async () => {
    const asked = await openSignedIn("/stock/materials/5", signedIn(), opened(GTIN));

    await screen.findByText(GTIN);
    const dialog = await editDialog();
    const gtin = within(dialog).getByRole("textbox", { name: "GTIN" });
    expect((gtin as HTMLInputElement).value).toBe(GTIN);
    fireEvent.change(gtin, { target: { value: "012345678905" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    expect((await saved(asked)).gtin).toBe("012345678905");
  });

  it("is left off its page while it has none, and still asked for", async () => {
    await openSignedIn("/stock/materials/5", signedIn(), opened(null));

    await screen.findByRole("heading", { name: "Oolong" });
    expect(screen.queryByText("GTIN")).toBeNull();
    const dialog = await editDialog();
    within(dialog).getByRole("textbox", { name: "GTIN" });
  });

  it("is neither shown nor asked for, nor changed, once its layout hides it", async () => {
    const asked = await openSignedIn(
      "/stock/materials/5",
      signedIn(),
      opened(null, [WITHOUT_GTIN])
    );

    await screen.findByRole("heading", { name: "Oolong" });
    const dialog = await editDialog();
    expect(within(dialog).queryByRole("textbox", { name: "GTIN" })).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    expect("gtin" in (await saved(asked))).toBe(false);
  });

  it("stays asked for where its layout hides it, once it has one", async () => {
    await openSignedIn("/stock/materials/5", signedIn(), opened(GTIN, [WITHOUT_GTIN]));

    await screen.findByRole("heading", { name: "Oolong" });
    // Its page follows the changed layout once it has loaded.
    await waitFor(() => expect(screen.queryByText(GTIN)).toBeNull());
    const dialog = await editDialog();
    within(dialog).getByRole("textbox", { name: "GTIN" });
  });
});
