// Holds ready to check: the lots on hold whose ready date has come, from
// Hoard's `holds-ready` endpoint, as a table. Initiative runs this in its
// widget sandbox, which calls `render` and draws the scene it returns.

var WORDS = {
  en: {
    nothing: "No holds ready to check",
    lot: "Lot",
    item: "Item",
    reason: "Reason",
    readyOn: "Ready on",
    reasons: {
      curing: "Curing",
      awaiting_results: "Awaiting test results",
      suspect: "Suspect",
      recall: "Recall",
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
        { key: "lot", label: words.lot },
        { key: "item", label: words.item },
        { key: "reason", label: words.reason },
        { key: "ready_on", label: words.readyOn },
      ],
      rows: rows.map(function (row) {
        return {
          lot: row.lot,
          item: row.item,
          reason: words.reasons[row.reason] || row.reason,
          ready_on: { value: row.ready_on, tone: "warning" },
        };
      }),
    },
  };
}
