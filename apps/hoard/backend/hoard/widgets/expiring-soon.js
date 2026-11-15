// Expiring soon: the lots of materials, bases and products that expire within
// 30 days, from Hoard's `expiring-soon` endpoint, as a table, soonest first,
// with what is left of each. A lot already expired is marked, and so is one
// on hold. Initiative runs this in its widget sandbox, which calls `render`
// and draws the scene it returns.

var WORDS = {
  en: {
    nothing: "Nothing expiring soon",
    lot: "Lot",
    item: "Item",
    expiresOn: "Expires on",
    left: "Left",
    onHold: "On hold",
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
        { key: "expires_on", label: words.expiresOn },
        { key: "left", label: words.left, align: "end", format: "plain" },
        { key: "on_hold", label: words.onHold },
      ],
      rows: rows.map(function (row) {
        return {
          lot: row.lot,
          item: row.unit ? row.item + " (" + row.unit + ")" : row.item,
          expires_on: { value: row.expires_on, tone: row.days_left <= 0 ? "negative" : "warning" },
          left: Number(row.left),
          on_hold: row.on_hold ? { value: true, tone: "warning" } : false,
        };
      }),
    },
  };
}
