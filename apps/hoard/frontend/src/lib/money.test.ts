import { describe, expect, it } from "vitest";

import { formatMoney } from "@/lib/money";

describe("formatMoney", () => {
  it("shows a total in the currency's own places", () => {
    expect(formatMoney("1234.5", "USD", "en")).toBe("$1,234.50");
    expect(formatMoney("1234.5", "JPY", "en")).toBe("¥1,235");
  });

  it("shows an exact amount to every place it holds, without losing any", () => {
    expect(formatMoney("0.003125", "EUR", "en", { exact: true })).toBe("€0.003125");
    expect(formatMoney("98765432109876.123456", "USD", "en", { exact: true })).toBe(
      "$98,765,432,109,876.123456"
    );
  });
});
