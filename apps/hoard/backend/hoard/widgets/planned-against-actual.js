// Planned against actual: what each completed production run planned to
// make and made, from Hoard's `yield` endpoint. Initiative runs this in its widget sandbox, which calls
// `render` and draws the scene it returns.

var WORDS = {
  en: { nothing: "No completed runs yet" },
};

function render(_data, _config, context) {
  var words = WORDS[String((context && context.locale) || "en").split("-")[0]] || WORDS.en;
  return { v: 1, scene: { kind: "empty", message: words.nothing } };
}
