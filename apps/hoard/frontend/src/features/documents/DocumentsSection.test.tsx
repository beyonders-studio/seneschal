import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DocumentRead } from "@/api/generated/hoard.schemas";
import { lotRead } from "@/features/lots/testing";
import { type Answer, openSignedIn, signedIn } from "@/testing";

// The picker scrolls the option it marks into view, which jsdom can't.
Element.prototype.scrollIntoView ??= () => {};
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const linked = (fields: Partial<DocumentRead>): DocumentRead => ({
  id: 1,
  file_id: 31,
  name: "Complaint, 3 Nov",
  file_type: "upload",
  created_at: "2026-11-03T10:00:00Z",
  created_by: "member-ref",
  ...fields,
});

const CONSENT_REQUIRED: Answer = { status: 403, data: { detail: "FILES_CONSENT_REQUIRED" } };

/** Opens lot 3's page, its documents and files answered from `answers`. */
const openLot = (answers: Record<string, Answer>, readOnly = false) =>
  openSignedIn("/stock/lots/3", signedIn({ read_only: readOnly }), {
    "GET /api/v1/lots/3": { status: 200, data: lotRead() },
    ...answers,
  });

describe("DocumentsSection", () => {
  it("lists a lot's files together, naming only those the member may open", async () => {
    const asked = await openLot({
      "GET /api/v1/documents": {
        status: 200,
        data: [linked({}), linked({ id: 2, file_id: 66, name: null })],
      },
      "DELETE /api/v1/documents/1": { status: 204 },
    });

    await screen.findByText("Complaint, 3 Nov");
    screen.getByText("A file you can't open");
    expect(asked.find((each) => each.url === "/api/v1/documents")?.params).toEqual({ lot_id: 3 });

    fireEvent.click(screen.getByRole("button", { name: "Unlink Complaint, 3 Nov" }));
    await waitFor(() => expect(asked.some((each) => each.method === "delete")).toBe(true));
  });

  it("links a file the member finds in Initiative", async () => {
    const asked = await openLot({
      "GET /api/v1/documents": { status: 200, data: [] },
      "GET /api/v1/files": {
        status: 200,
        data: [
          { id: 40, name: "Traceability plan", file_type: "lexical", initiative_name: "Kitchen" },
        ],
      },
      "POST /api/v1/documents": { status: 201, data: linked({ id: 3, file_id: 40 }) },
    });

    await screen.findByText("No documents linked yet.");
    fireEvent.click(screen.getByRole("combobox", { name: "Thing" }));
    fireEvent.click(await screen.findByRole("option", { name: /Traceability plan/ }));

    await waitFor(() => {
      const posted = asked.find(
        (each) => each.method === "post" && each.url === "/api/v1/documents"
      );
      expect(JSON.parse(String(posted?.data))).toEqual({ file_id: 40, lot_id: 3 });
    });
  });

  it("asks the member to let Hoard read their files", async () => {
    const asked = await openLot({
      "GET /api/v1/documents": CONSENT_REQUIRED,
      "POST /api/v1/files/consent": { status: 200, data: { status: "pending" } },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Ask in Initiative" }));

    await screen.findByText(/Initiative has asked you to let Hoard read your files/);
    expect(asked.some((each) => each.url === "/api/v1/files/consent")).toBe(true);
  });

  it("shows a read-only community's files without a way to change them", async () => {
    await openLot({ "GET /api/v1/documents": { status: 200, data: [linked({})] } }, true);

    await screen.findByText("Complaint, 3 Nov");
    expect(screen.queryByRole("button", { name: /Unlink/ })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Thing" })).toBeNull();
  });
});
