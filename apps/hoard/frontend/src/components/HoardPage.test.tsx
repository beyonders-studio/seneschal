import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { openSignedIn, signedIn } from "@/testing";

describe("HoardPage", () => {
  it("says when Hoard is read-only", async () => {
    await openSignedIn("/stock", signedIn({ read_only: true }));
    await screen.findByRole("heading", { name: "Stock" });
    screen.getByText("Hoard is read-only");
  });

  it("says nothing of it otherwise", async () => {
    await openSignedIn("/stock");
    await screen.findByRole("heading", { name: "Stock" });
    expect(screen.queryByText("Hoard is read-only")).toBeNull();
  });
});
