// What to make next: each product and base to make, from Hoard's `make-next`
// endpoint, as a table: what is needed of it, what there is, and the whole
// batches of its recipe that make up the rest, with the hours they take.
// Initiative runs this in its widget sandbox, which calls `render` and draws
// the scene it returns.

var WORDS = {
  en: {
    nothing: "Nothing to make",
    item: "What to make",
    needed: "Needed",
    available: "Available",
    curing: "Curing",
    planned: "In planned runs",
    batches: "Batches",
    hours: "Hours",
    base: "base",
  },
};

function render(data, _config, context) {
  var words = WORDS[String((context && context.locale) || "en").split("-")[0]] || WORDS.en;
  var rows = (data && data.rows) || [];
  if (!rows.length) return { v: 1, scene: { kind: "empty", message: words.nothing } };
  return {
    v: 1,
    scene: {
      kind: "table",
      columns: [
        { key: "item", label: words.item },
        { key: "needed", label: words.needed, align: "end", format: "plain" },
        { key: "available", label: words.available, align: "end", format: "plain" },
        { key: "curing", label: words.curing, align: "end", format: "plain" },
        { key: "planned", label: words.planned, align: "end", format: "plain" },
        { key: "batches", label: words.batches, align: "end", format: "plain" },
        { key: "hours", label: words.hours, align: "end", format: "plain" },
      ],
      rows: rows.map(function (row) {
        var details = [];
        if (row.kind === "base") details.push(words.base);
        if (row.unit) details.push(row.unit);
        var available = Number(row.available);
        return {
          item: details.length ? row.item + " (" + details.join(", ") + ")" : row.item,
          needed: Number(row.needed),
          available: available < 0 ? { value: available, tone: "negative" } : available,
          curing: Number(row.curing),
          planned: Number(row.planned),
          batches: { value: row.batches, tone: "accent" },
          hours: Number(row.hours),
        };
      }),
    },
  };
}
