// Waste by reason: what was thrown away, by reason, from Hoard's `waste`
// endpoint. Initiative runs this in its widget sandbox, which calls
// `render` and draws the scene it returns.

var WORDS = {
  en: { nothing: "Nothing wasted yet" },
};

function render(_data, _config, context) {
  var words = WORDS[String((context && context.locale) || "en").split("-")[0]] || WORDS.en;
  return { v: 1, scene: { kind: "empty", message: words.nothing } };
}
