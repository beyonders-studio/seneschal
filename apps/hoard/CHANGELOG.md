# Changelog

All notable changes to Hoard are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Hoard uses
[semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- **Bases and recipes.** A base is something you make to use in other things, like a spice blend or cold brew. Recipes hold materials and bases, nest as deep as you need, scale to any batch, and show each line's share by weight. A bundle, such as a gift box, holds the products inside it.
- **Production runs.** Plan a run from its recipe, start it, and complete it with what you actually made: the materials it used come out of stock, and what it made goes in as a lot named for the run. Lots you don't pick are taken earliest expiry first. Reopen a run to take it back. A run is marked uncosted when a material it used had no cost.
- **Runs and purchases on your project's tasks.** A task can track a run or a purchase and show its status, and **Complete run** and **Received in full** act on it from the task. Automations can make and link those tasks, and start flows when a run starts or finishes, a purchase is placed or received, or stock runs low.
- **Ingredients and allergens.** Mark each material's allergens, or a delivered lot's own. Products and bases roll them up through their recipes, a run's lots show what went in, and ingredients list heaviest first. A material nobody has checked shows as undeclared.
- **Choose your kind of business when you first open Hoard.** Admins pick it from a list, and Hoard starts with the units, categories, lot details and waste reasons it uses; "Something else" starts blank. Tea, coffee and drinks is the first, and adds waste by reason, planned against actual, and an allergen matrix to Reports.
- **Stock, Production and Reports pages in each initiative Hoard is placed in, and Settings for community admins.** Hoard keeps one set of stock for the whole community, whichever initiative it's opened from.
- **Materials, products and variations,** with SKUs, categories, purchase and tracking units, low-stock limits and product pictures. Each record page shows stock and lots per location, and who added and last changed it.
- **Purchases from your community's contacts.** Receiving a line makes its lot and moves the material's average cost. Discount and shipping are spread over the lines by value, and tax is kept out. Add a new supplier from the picker without leaving the purchase.
- **Adjustments and stocktakes.** Record a change with a reason, or count materials or products (all, a sample, or one category) and confirm each row. Each stocktake shows its accuracy.
- **Reports:** what to reorder and what your stock is worth, plus CSV and JSON exports of every record.
- **Widgets** for stock to reorder, stock value, waste by reason, planned against actual and the allergen matrix, and a **Stock overview dashboard.**
- **Your own properties and layouts.** Admins add properties to materials, products and lots, and choose which fields each list and record page shows. Everyone fills them in.
- **Settings** for units, categories, locations, adjustment reasons and your currency.
- **"See costs and prices",** a permission your community's roles hold. Without it, costs and prices stay hidden.

