// Stock to reorder: each material and product at or below its low-stock limit,
// from Hoard's `reorder` endpoint, as a table. Initiative runs this in its
// widget sandbox, which calls `render` and draws the scene it returns.

var WORDS = {
  en: {
    nothing: "Nothing to reorder",
    item: "Item",
    onHand: "On hand",
    limit: "Low-stock limit",
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
        { key: "on_hand", label: words.onHand, align: "end", format: "plain" },
        { key: "limit", label: words.limit, align: "end", format: "plain" },
      ],
      rows: rows.map(function (row) {
        var onHand = Number(row.on_hand);
        return {
          item: row.unit ? row.name + " (" + row.unit + ")" : row.name,
          on_hand: { value: onHand, tone: onHand > 0 ? "warning" : "negative" },
          limit: Number(row.low_stock_limit),
        };
      }),
    },
  };
}
