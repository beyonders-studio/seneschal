// Waste by reason: what was thrown away, by reason, from Hoard's `waste`
// endpoint, as a table. Initiative runs this in its widget sandbox, which calls
// `render` and draws the scene it returns.

var WORDS = {
  en: {
    nothing: "Nothing wasted yet",
    reason: "Reason",
    item: "Item",
    quantity: "Quantity",
    reasons: {
      correction: "Correction",
      damaged: "Damaged",
      expired: "Expired",
      lost: "Lost",
      personal_use: "Personal use",
      returned_damaged: "Returned damaged",
    },
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
        { key: "reason", label: words.reason },
        { key: "item", label: words.item },
        { key: "quantity", label: words.quantity, align: "end", format: "plain" },
      ],
      rows: rows.map(function (row) {
        return {
          reason: words.reasons[row.reason] || row.reason,
          item: row.unit ? row.item + " (" + row.unit + ")" : row.item,
          quantity: Number(row.quantity),
        };
      }),
    },
  };
}
