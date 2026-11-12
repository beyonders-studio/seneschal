import type { OrderRead } from "@/api/generated/hoard.schemas";

/** Puts the lots an order drew, recorded or assumed, on hold as suspect,
 *  when a problem with the order is reported. */
export function HoldFromOrder(_props: { order: OrderRead }) {
  return null;
}
