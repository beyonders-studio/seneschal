// Cost to make: what one unit of each product cost over its last two
// completed runs, from Hoard's `cost-to-make` endpoint, as a table beside the
// store's price and the margin it leaves, in the organization's currency. A
// cost that rose is marked. Initiative runs this in its widget sandbox, which
// calls `render` and draws the scene it returns.

var WORDS = {
  en: {
    nothing: "No completed runs yet",
    product: "Product",
    last: "Last run",
    previous: "Run before",
    change: "Change",
    price: "Store price",
    margin: "Margin",
  },
};

function amount(value) {
  return value === null || value === undefined ? null : Number(value);
}

function render(data, _config, context) {
  var words = WORDS[String((context && context.locale) || "en").split("-")[0]] || WORDS.en;
  var rows = (data && data.rows) || [];
  if (!rows.length) return { v: 1, scene: { kind: "empty", message: words.nothing } };
  var currency = (data.values && data.values.currency) || "";
  function money(label) {
    return currency ? label + " (" + currency + ")" : label;
  }
  return {
    v: 1,
    scene: {
      kind: "table",
      columns: [
        { key: "product", label: words.product },
        { key: "last", label: money(words.last), align: "end", format: "plain" },
        { key: "previous", label: money(words.previous), align: "end", format: "plain" },
        { key: "change", label: words.change, align: "end", format: "plain" },
        { key: "price", label: money(words.price), align: "end", format: "plain" },
        { key: "margin", label: words.margin, align: "end", format: "percent" },
      ],
      rows: rows.map(function (row) {
        var change = amount(row.change);
        var margin = amount(row.margin);
        return {
          product: row.product,
          last: amount(row.last),
          previous: amount(row.previous),
          change:
            change === null ? null : { value: change, tone: change > 0 ? "negative" : "positive" },
          price: amount(row.price),
          margin: margin === null ? null : { value: margin, tone: margin < 0 ? "negative" : "neutral" },
        };
      }),
    },
  };
}
