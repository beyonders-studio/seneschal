// Materials to order: each material the runs ahead need more of than is
// available and on order, from Hoard's `materials-to-order` endpoint, as a
// table: its supplier, how much is short, the day it is needed and the day to
// order it by. Initiative runs this in its widget sandbox, which calls
// `render` and draws the scene it returns.

var WORDS = {
  en: {
    nothing: "Nothing to order",
    material: "Material",
    supplier: "Supplier",
    short: "Short",
    neededOn: "Needed on",
    orderBy: "Order by",
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
        { key: "material", label: words.material },
        { key: "supplier", label: words.supplier },
        { key: "short", label: words.short, align: "end", format: "plain" },
        { key: "needed_on", label: words.neededOn },
        { key: "order_by", label: words.orderBy },
      ],
      rows: rows.map(function (row) {
        return {
          material: row.unit ? row.material + " (" + row.unit + ")" : row.material,
          supplier: row.supplier,
          short: { value: Number(row.short), tone: "negative" },
          needed_on: row.needed_on,
          order_by: row.order_by ? { value: row.order_by, tone: "warning" } : null,
        };
      }),
    },
  };
}
