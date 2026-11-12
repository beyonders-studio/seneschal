import type { LotRead } from "@/api/generated/hoard.schemas";

/** A lot of a material, not on hold, as its page reads it. */
export const lotRead = (fields: Partial<LotRead> = {}): LotRead => ({
  id: 3,
  location_id: 10,
  number: "L-3",
  dated_on: "2026-11-01",
  expires_on: null,
  notes: null,
  balance: "10.000000",
  on_hold: false,
  properties: [],
  item: { id: 5, kind: "material", name: "Oolong", attributes: {} },
  product_id: null,
  purchase_line_id: null,
  hold: null,
  created_at: "2026-11-01T10:00:00Z",
  created_by: "member-ref",
  updated_at: "2026-11-01T10:00:00Z",
  updated_by: "member-ref",
  ...fields,
});
