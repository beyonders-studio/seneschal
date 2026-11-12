import type { TraceStepRead } from "@/api/generated/hoard.schemas";

/** A step of a trace: a run that drew on lot L-3 and made lot R-7. */
export const traceStep = (fields: Partial<TraceStepRead> = {}): TraceStepRead => ({
  kind: "run",
  record_id: 7,
  code: "R-7",
  order_id: null,
  lot: { id: 3, number: "L-3" },
  made_lot: { id: 8, number: "R-7" },
  from_lot: { id: 3, number: "L-3" },
  item: { id: 5, kind: "material", name: "Oolong", attributes: {} },
  quantity: "2.000000",
  dated_on: "2026-11-03",
  party: null,
  assumed: false,
  level: 1,
  ...fields,
});
