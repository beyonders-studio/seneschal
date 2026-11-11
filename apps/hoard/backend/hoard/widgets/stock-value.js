// Stock value: what the stock on hand is worth at its average cost, from
// Hoard's `valuation` endpoint, with the organization's currency beneath.
// Initiative runs this in its widget sandbox, which calls `render` and draws
// the scene it returns.

var WORDS = {
  en: { label: "Stock value", nothing: "No stock valued yet" },
};

function render(data, _config, context) {
  var words = WORDS[String((context && context.locale) || "en").split("-")[0]] || WORDS.en;
  var values = (data && data.values) || {};
  if (values.total === undefined || values.total === null) {
    return { v: 1, scene: { kind: "empty", message: words.nothing } };
  }
  return {
    v: 1,
    scene: {
      kind: "metric",
      value: Number(values.total),
      label: words.label,
      format: "plain",
      caption: values.currency,
    },
  };
}
