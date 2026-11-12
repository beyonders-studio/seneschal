// Allergen matrix: which allergens each product contains, from Hoard's
// `allergen-matrix` endpoint, as a table with a column for each allergen some
// product contains. Initiative runs this in its widget sandbox, which calls
// `render` and draws the scene it returns.

var ALLERGENS = [
  "celery",
  "crustaceans",
  "eggs",
  "fish",
  "gluten",
  "lupin",
  "milk",
  "molluscs",
  "mustard",
  "peanuts",
  "sesame",
  "soybeans",
  "sulphites",
  "tree_nuts",
  "wheat",
];

var WORDS = {
  en: {
    nothing: "No products yet",
    none: "No product contains an allergen",
    product: "Product",
    contains: "Contains",
    allergens: {
      celery: "Celery",
      crustaceans: "Crustaceans",
      eggs: "Eggs",
      fish: "Fish",
      gluten: "Gluten",
      lupin: "Lupin",
      milk: "Milk",
      molluscs: "Molluscs",
      mustard: "Mustard",
      peanuts: "Peanuts",
      sesame: "Sesame",
      soybeans: "Soy",
      sulphites: "Sulphites",
      tree_nuts: "Tree nuts",
      wheat: "Wheat",
    },
  },
};

function render(data, _config, context) {
  var words = WORDS[String((context && context.locale) || "en").split("-")[0]] || WORDS.en;
  var rows = (data && data.rows) || [];
  if (!rows.length) return { v: 1, scene: { kind: "empty", message: words.nothing } };
  var present = ALLERGENS.filter(function (allergen) {
    return rows.some(function (row) {
      return row[allergen] === true;
    });
  });
  if (!present.length) return { v: 1, scene: { kind: "empty", message: words.none } };
  return {
    v: 1,
    scene: {
      kind: "table",
      columns: [{ key: "product", label: words.product }].concat(
        present.map(function (allergen) {
          return { key: allergen, label: words.allergens[allergen] };
        }),
      ),
      rows: rows.map(function (row) {
        var cells = { product: row.product };
        present.forEach(function (allergen) {
          cells[allergen] = row[allergen] === true ? { value: words.contains, tone: "warning" } : null;
        });
        return cells;
      }),
    },
  };
}
