// Planned against actual: what each completed production run planned to
// make and made, from Hoard's `yield` endpoint, as a table. A run that made
// less than it planned is marked. Initiative runs this in its widget sandbox,
// which calls `render` and draws the scene it returns.

var WORDS = {
  en: {
    nothing: "No completed runs yet",
    run: "Run",
    item: "What it made",
    planned: "Planned",
    actual: "Actual",
    yield: "Yield",
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
        { key: "run", label: words.run },
        { key: "item", label: words.item },
        { key: "planned", label: words.planned, align: "end", format: "plain" },
        { key: "actual", label: words.actual, align: "end", format: "plain" },
        { key: "yield", label: words.yield, align: "end", format: "percent" },
      ],
      rows: rows.map(function (row) {
        var planned = row.planned === null || row.planned === undefined ? null : Number(row.planned);
        var actual = Number(row.actual);
        var share = planned ? actual / planned : null;
        return {
          run: row.code,
          item: row.unit ? row.item + " (" + row.unit + ")" : row.item,
          planned: planned,
          actual: actual,
          yield: share === null ? null : { value: share, tone: share < 1 ? "warning" : "positive" },
        };
      }),
    },
  };
}
