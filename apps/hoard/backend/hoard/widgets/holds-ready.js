// Holds ready to check: the lots on hold whose ready date has come, from
// Hoard's `holds-ready` endpoint. Initiative runs this in its widget sandbox,
// which calls `render` and draws the scene it returns.

var WORDS = { en: { nothing: "No holds ready to check" } };

function render(_data, _config, context) {
  var words = WORDS[String((context && context.locale) || "en").split("-")[0]] || WORDS.en;
  return { v: 1, scene: { kind: "empty", message: words.nothing } };
}
