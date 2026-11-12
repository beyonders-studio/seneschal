import type {
  OrderLineRead,
  OrderRead,
  OrderShipmentLineRead,
  OrderShipmentRead,
} from "@/api/generated/hoard.schemas";

/** A lot a shipment line may draw. */
export const orderLot = { id: 4, number: "C-1", expires_on: null, balance: "10.000000" };

/** An order's line: 4 of Chai at $5, nothing of it shipped. */
export const orderLine = (overrides: Partial<OrderLineRead> = {}): OrderLineRead => ({
  id: 9,
  item: { id: 3, kind: "variation", name: "Chai", attributes: {} },
  quantity: "4.000000",
  price: "5.000000",
  bundle_line_id: null,
  held: "0.000000",
  shipped: "0.000000",
  production_run_id: null,
  selling_plan: null,
  ...overrides,
});

/** A shipment's line, of the order line `orderLine` makes, drawing no lot yet. */
export const shipmentLine = (
  overrides: Partial<OrderShipmentLineRead> = {}
): OrderShipmentLineRead => ({
  id: 11,
  order_line_id: 9,
  item: { id: 3, kind: "variation", name: "Chai", attributes: {} },
  quantity: "4.000000",
  lot: null,
  assumed: false,
  lots: [orderLot],
  returns: [],
  ...overrides,
});

/** A pending shipment of the whole of `orderLine`. */
export const orderShipment = (overrides: Partial<OrderShipmentRead> = {}): OrderShipmentRead => ({
  id: 12,
  location_id: 1,
  status: "pending",
  shipped_on: null,
  external_id: null,
  carrier_contact_id: null,
  tracking: null,
  delivery_address: null,
  reference: null,
  lines: [shipmentLine()],
  ...overrides,
});

/** An open hand order, `O-0005`, for contact 4. */
export const orderRead = (overrides: Partial<OrderRead> = {}): OrderRead => ({
  id: 5,
  code: "O-0005",
  store_id: null,
  external_id: null,
  external_number: null,
  customer_contact_id: 4,
  location_id: 1,
  initiative_id: 7,
  ordered_on: "2026-11-02",
  due_on: null,
  paid: false,
  status: "open",
  shipping: "0.000000",
  tax: "0.000000",
  discount: "0.000000",
  total: "20.000000",
  delivery_address: null,
  lines: [orderLine()],
  shipments: [],
  created_at: "2026-11-01T09:00:00Z",
  created_by: "member-ref",
  updated_at: "2026-11-01T09:00:00Z",
  updated_by: "member-ref",
  ...overrides,
});
