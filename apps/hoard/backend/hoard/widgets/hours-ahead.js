// Production hours ahead: the hours of work in each of the next four weeks,
// from Hoard's `hours-ahead` endpoint, as stacked bars: the planned and
// started runs, and the suggested ones. Initiative runs this in its widget
// sandbox, which calls `render` and draws the scene it returns.

var WORDS = {
  en: {
    nothing: "No production ahead",
    planned: "Planned and started runs",
    suggested: "Suggested runs",
    hours: "Hours",
  },
};

function render(data, _config, context) {
  var words = WORDS[String((context && context.locale) || "en").split("-")[0]] || WORDS.en;
  var rows = (data && data.rows) || [];
  var any = rows.some(function (row) {
    return Number(row.planned) > 0 || Number(row.suggested) > 0;
  });
  if (!any) return { v: 1, scene: { kind: "empty", message: words.nothing } };
  function points(key) {
    return rows.map(function (row) {
      return { x: Date.parse(row.week), y: Number(row[key]) };
    });
  }
  return {
    v: 1,
    scene: {
      kind: "series",
      mark: "bar",
      stacked: true,
      xTime: "week",
      yLabel: words.hours,
      format: "plain",
      showLegend: true,
      series: [
        { name: words.planned, points: points("planned"), tone: "series-1" },
        { name: words.suggested, points: points("suggested"), tone: "series-2" },
      ],
    },
  };
}
