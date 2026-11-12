import type {
  BaseDetail,
  BaseRead,
  MaterialDetail,
  MaterialRead,
  ProductDetail,
  ProductRead,
} from "@/api/generated/hoard.schemas";
import type { Answer } from "@/testing";

// The kit's switches measure themselves, which jsdom cannot.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

/** The settings item pages read: a unit, a category of each kind, the main
 *  location and one more, and the currency. */
export const SETTINGS: Record<string, Answer> = {
  "GET /api/v1/settings": { status: 200, data: { currency: "USD", auto_manufacture: "off" } },
  "GET /api/v1/settings/units": { status: 200, data: [{ id: 1, name: "g" }] },
  "GET /api/v1/settings/categories": {
    status: 200,
    data: [
      { id: 2, kind: "material", name: "Tea" },
      { id: 3, kind: "product", name: "Drinks" },
    ],
  },
  "GET /api/v1/settings/locations": {
    status: 200,
    data: [
      { id: 10, name: null, main: true },
      { id: 11, name: "Back room", main: false },
    ],
  },
  "GET /api/v1/members": { status: 200, data: [{ member_ref: "member-ref", name: "Ada" }] },
};

const STAMPS = {
  created_at: "2026-11-01T10:00:00Z",
  created_by: "member-ref",
  updated_at: "2026-11-02T10:00:00Z",
  updated_by: "member-ref",
};

export const material = (fields: Partial<MaterialRead> = {}): MaterialRead => ({
  id: 5,
  name: "Oolong",
  sku: "OOL-1",
  inventoriable: true,
  tracking_unit_id: 1,
  purchase_unit_id: null,
  purchase_conversion: "1.000000",
  low_stock_limit: null,
  category_id: 2,
  supplier_contact_id: null,
  supplier_code: null,
  on_hand: "0",
  average_cost: null,
  properties: [],
  ...fields,
});

export const materialDetail = (fields: Partial<MaterialDetail> = {}): MaterialDetail => ({
  ...material(),
  ...STAMPS,
  stock: [],
  lots: [],
  ...fields,
});

export const product = (fields: Partial<ProductRead> = {}): ProductRead => ({
  id: 7,
  name: "Chai",
  status: "active",
  category_id: 3,
  low_stock_limit: null,
  on_hand: "0",
  default_price: null,
  auto_manufacture: null,
  properties: [],
  ...fields,
});

export const productDetail = (fields: Partial<ProductDetail> = {}): ProductDetail => ({
  ...product(),
  ...STAMPS,
  variations: [],
  ...fields,
});

export const base = (fields: Partial<BaseRead> = {}): BaseRead => ({
  id: 8,
  name: "Chai spice",
  sku: null,
  tracking_unit_id: 1,
  low_stock_limit: null,
  category_id: null,
  on_hand: "0",
  average_cost: null,
  properties: [],
  ...fields,
});

export const baseDetail = (fields: Partial<BaseDetail> = {}): BaseDetail => ({
  ...base(),
  ...STAMPS,
  stock: [],
  lots: [],
  ...fields,
});
