// Sales by week: the units of each product sold each week over the last 12
// weeks, from Hoard's `sales-by-week` endpoint, as a line for each product.
// Where there are more products than a chart draws lines, the ones that sold
// least are drawn together. Initiative runs this in its widget sandbox, which
// calls `render` and draws the scene it returns.

var WORDS = {
  en: { nothing: "No sales in the last 12 weeks", units: "Units sold", others: "Others" },
};

// The most lines a chart draws.
var MAX_SERIES = 24;

function render(data, _config, context) {
  var words = WORDS[String((context && context.locale) || "en").split("-")[0]] || WORDS.en;
  var rows = (data && data.rows) || [];
  if (!rows.length) return { v: 1, scene: { kind: "empty", message: words.nothing } };
  var byProduct = {};
  var order = [];
  rows.forEach(function (row) {
    if (!byProduct[row.product]) {
      byProduct[row.product] = { name: row.product, total: 0, weeks: {} };
      order.push(row.product);
    }
    var units = Number(row.units);
    byProduct[row.product].total += units;
    byProduct[row.product].weeks[row.week] = units;
  });
  var weeks = [];
  rows.forEach(function (row) {
    if (weeks.indexOf(row.week) < 0) weeks.push(row.week);
  });
  var products = order.map(function (name) {
    return byProduct[name];
  });
  if (products.length > MAX_SERIES) {
    products.sort(function (a, b) {
      return b.total - a.total;
    });
    var others = { name: words.others, total: 0, weeks: {} };
    products.slice(MAX_SERIES - 1).forEach(function (product) {
      weeks.forEach(function (week) {
        others.weeks[week] = (others.weeks[week] || 0) + (product.weeks[week] || 0);
      });
    });
    products = products.slice(0, MAX_SERIES - 1).concat([others]);
  }
  return {
    v: 1,
    scene: {
      kind: "series",
      mark: "line",
      xTime: "week",
      yLabel: words.units,
      format: "plain",
      showLegend: true,
      series: products.map(function (product) {
        return {
          name: product.name,
          points: weeks.map(function (week) {
            return { x: Date.parse(week), y: product.weeks[week] || 0 };
          }),
        };
      }),
    },
  };
}
