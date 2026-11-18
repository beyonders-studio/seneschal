import type {
  BaseDetail,
  BaseRead,
  ItemStockRead,
  ItemUsage,
  MaterialDetail,
  MaterialRead,
  ProductDetail,
  ProductRead,
  ProductVariationRead,
} from "@/api/generated/hoard.schemas";
import type { Answer } from "@/testing";

// The kit's switches measure themselves, which jsdom cannot.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

/** The settings item pages read: a unit, a category of each kind, each
 *  kind's default type, the main location and one more, and the currency. */
export const SETTINGS: Record<string, Answer> = {
  "GET /api/v1/settings": {
    status: 200,
    data: { currency: "USD", auto_manufacture: "off", regimes: [] },
  },
  "GET /api/v1/settings/units": {
    status: 200,
    data: [{ id: 1, name: "g", standard: "gram", measure: "mass", aliases: ["g", "grams"] }],
  },
  "GET /api/v1/settings/categories": {
    status: 200,
    data: [
      { id: 2, kind: "material", name: "Tea", picking: "earliest_expiry", default_type_id: null },
      { id: 3, kind: "product", name: "Drinks", picking: "earliest_expiry", default_type_id: null },
    ],
  },
  "GET /api/v1/settings/types": {
    status: 200,
    data: [
      { id: 30, kind: "material", name: null, shape: "ingredient", is_default: true },
      { id: 31, kind: "base", name: null, shape: "base", is_default: true },
      { id: 32, kind: "product", name: null, shape: "made", is_default: true },
    ],
  },
  "GET /api/v1/settings/locations": {
    status: 200,
    data: [
      { id: 10, name: null, main: true, contact_id: null },
      { id: 11, name: "Back room", main: false, contact_id: null },
    ],
  },
  "GET /api/v1/members": { status: 200, data: [{ member_ref: "member-ref", name: "Ada" }] },
};

/** The windows usage is read over, each with nothing received or used. */
const NO_USAGE: ItemUsage[] = (
  ["week", "month", "months_3", "months_6", "months_12", "all"] as const
).map((window) => ({ window, received: "0", used: "0" }));

/** An item's figures with no recipe, run, stocktake or purchase behind them. */
const FIGURES = {
  consigned: "0",
  committed: "0",
  available: "0",
  potential: null,
  made: "0",
  last_stocktake: null,
  on_order: "0",
  overdue: false,
  usage: NO_USAGE,
  used_in: [],
};

/** A row of an item's stock table: what is on hand at a location. */
export const stockRow = (
  location_id: number,
  quantity: string,
  fields: Partial<ItemStockRead> = {}
): ItemStockRead => ({
  location_id,
  quantity,
  consigned: "0",
  bin: null,
  could_make: null,
  last_stocktake: null,
  ...fields,
});

const STAMPS = {
  created_at: "2026-11-01T10:00:00Z",
  created_by: "member-ref",
  updated_at: "2026-11-02T10:00:00Z",
  updated_by: "member-ref",
};

export const material = (fields: Partial<MaterialRead> = {}): MaterialRead => ({
  id: 5,
  name: "Oolong",
  status: "active",
  notes: null,
  sku: "OOL-1",
  gtin: null,
  inventoriable: true,
  tracking_unit_id: 1,
  purchase_unit_id: null,
  purchase_conversion: "1.000000",
  unit_weight: null,
  low_stock_limit: null,
  category_id: 2,
  type_id: 30,
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
  ...FIGURES,
  stock: [],
  lots: [],
  picture_id: null,
  ...fields,
});

export const product = (fields: Partial<ProductRead> = {}): ProductRead => ({
  id: 7,
  name: "Chai",
  status: "active",
  category_id: 3,
  type_id: 32,
  low_stock_limit: null,
  on_hand: "0",
  default_price: null,
  auto_manufacture: null,
  bundle: false,
  cure_days: null,
  traceability_list: false,
  rinse_off: false,
  properties: [],
  ...fields,
});

export const productDetail = (fields: Partial<ProductDetail> = {}): ProductDetail => ({
  ...product(),
  ...STAMPS,
  totals: {
    consigned: "0",
    committed: "0",
    available: "0",
    potential: null,
    made: "0",
    sold: "0",
    shipped: "0",
    on_order: "0",
    overdue: false,
    profit: null,
    retail: { sold: "0", shipped: "0", profit: null },
    wholesale: null,
  },
  variations: [],
  store_products: [],
  ...fields,
});

export const variation = (fields: Partial<ProductVariationRead> = {}): ProductVariationRead => ({
  id: 20,
  attributes: {},
  sku: null,
  gtin: null,
  is_default: true,
  price: null,
  average_cost: null,
  auto_manufacture: null,
  supplier_contact_id: null,
  supplier_code: null,
  purchase_unit_id: null,
  purchase_conversion: "1.000000",
  on_hand: "0",
  ...FIGURES,
  sold: "0",
  shipped: "0",
  profit: null,
  pricing: null,
  stock: [],
  lots: [],
  ...fields,
});

export const base = (fields: Partial<BaseRead> = {}): BaseRead => ({
  id: 8,
  name: "Chai spice",
  status: "active",
  notes: null,
  sku: null,
  gtin: null,
  tracking_unit_id: 1,
  low_stock_limit: null,
  category_id: null,
  type_id: 31,
  cure_days: null,
  auto_manufacture: null,
  on_hand: "0",
  average_cost: null,
  properties: [],
  ...fields,
});

export const baseDetail = (fields: Partial<BaseDetail> = {}): BaseDetail => ({
  ...base(),
  ...STAMPS,
  ...FIGURES,
  stock: [],
  lots: [],
  picture_id: null,
  ...fields,
});
