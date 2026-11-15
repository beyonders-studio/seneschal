# Changelog

All notable changes to Hoard are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Hoard uses
[semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- **Sync with Shopify.** A newly connected store waits, untouched, until an admin chooses **Sync with Shopify** in Settings › Stores. A short guide explains what syncing does and lets you choose which Hoard location each store location's stock goes into. The sync then reads the store's products, its last 60 days of orders and its stock and costs into Hoard: matched products take the store's names and SKUs, the others are made in Hoard, and Hoard's counts are set to the store's. Nothing is sent to the store, and the sync carries on if you leave the page, with its progress shown in Settings. **Remove sync** stops it and keeps everything it brought in.
- **Hoard keeps a copy of each store product,** up to date as it changes in the store, so a product's store details show without asking the store each time. Products made in the store after the sync are made in Hoard too; one deleted in the store keeps its Hoard product.
- **Customer numbers on store orders.** Each Shopify order keeps the store's number for its customer, and nothing else about them. An order's page lists the customer's other orders, and the orders list filters by customer number. Hoard now asks the store's app for the `read_customers` permission.

### Changed

- **A synced store's orders are managed in the store.** Hoard shows them as they stand, with **Open in Shopify** to change them there; you still record which lot each shipment draws and what comes back. Orders you make by hand, and a store's orders while it isn't synced, change in Hoard as before.
- **Editing a store's product in Hoard writes to the store.** A new name, status, SKU or option on a product a synced store lists is saved to the store first, and archiving it archives it there. If the product changed in the store since you opened it, nothing is saved and Hoard shows the store's newer version. A product the store lists can't be deleted in Hoard: archive it, or delete it in the store first.
- A store's locations start with no Hoard location: the sync, or an admin, chooses where each one's stock goes.
- Exports run in the background: Hoard shows each one's progress, then offers its file to download once it's ready. Only the member who started an export can download it, for a week.
- Hoard's pages no longer repeat the tab's name as a heading above their own tabs.

### Fixed

- Changing a product's auto-manufacture setting no longer clears its cure time and its traceability flags.
- **Refunds and edits in your store reach Hoard.** A line refunded or removed before it ships stops holding stock, and the store is sent the new count; one refunded after it shipped stays shipped, and stock comes back when you record the return. An order's page shows what was ordered, what it sells now, and what was refunded.
- **Changes to orders older than 60 days are kept.** Shopify stops answering for an order after 60 days, so Hoard now keeps the change its webhook describes.
- **An older copy of an order never replaces a newer one,** when the store's webhooks arrive out of order.

## [0.1.2] - 2026-11-14

### Fixed

- Hoard starts on a server that runs it from the marketplace package: the image now carries the script that sets up its database logins.

## [0.1.1] - 2026-11-13

### Fixed

- Hoard can be listed in the marketplace: its first release's notes were longer than a listing holds.

## [0.1.0] - 2026-11-13

### Added

- **Bases and recipes.** A base is something you make to use in other things, like a spice blend or cold brew. Recipes hold materials and bases, nest as deep as you need, scale to any batch, and show each line's share by weight. A bundle, such as a gift box, holds the products inside it.
- **Production runs.** Plan a run from its recipe, start it, and complete it with what you actually made: the materials it used come out of stock, and what it made goes in as a lot named for the run. Lots you don't pick are taken by their category's picking rule. Reopen a run to take it back. A run is marked uncosted when a material it used had no cost.
- **Runs and purchases on your project's tasks.** A task can track a run or a purchase and show its status, and **Complete run** and **Received in full** act on it from the task. Automations can make and link those tasks, and start flows when a run starts or finishes, a purchase is placed or received, or stock runs low.
- **Ingredients and allergens.** Mark each material's allergens, or a delivered lot's own. Products and bases roll them up through their recipes, a run's lots show what went in, and ingredients list heaviest first. A material nobody has checked shows as undeclared.
- **Trace any lot back to its suppliers and forward to where it went.** Each lot has a page of its own: what went into it, down to the purchases and suppliers it came from, and everything made from it and every order that shipped it, step by step. Sales that named no lot show where it may have gone.
- **Recall in one action.** Recall a lot, or every lot of an item dated in a range, to see everything made from it and every shipment it reached, then download the FDA's sortable spreadsheet, Canada's plain-text file or a PDF.
- **Holds.** Put a lot on hold with a reason (curing, awaiting test results, suspect, recall or your own) and the date it's ready on. Held stock stays out of picking, runs and shipments and isn't counted as available, so your store and your orders don't sell it, and an order it leaves short shows as short. Release a hold once it's ready, or discard some or all of the lot. A run of something with a cure time puts what it made on hold to cure, and an order can put every lot it drew on hold, with everything made from them. A widget lists the holds ready to check.
- **Picking rules per category.** Each category says which lot goes first: earliest expiry, first in, last in, or always recorded, where every run and shipment names its lot.
- **Link Initiative files to lots, products, materials and bases,** such as certificates and test reports. Several on one lot list together, and each person sees only the files they can open in Initiative.
- **EU fragrance allergens.** Record each fragrance allergen's share in a material or a delivered lot. Products and lots list the ones past the threshold to declare, which is lower for a product left on the skin than for one rinsed off, and name the materials nobody has checked.
- **How long to keep your records.** Tick the rules you keep records for in Settings, and Hoard shows how long your backups need to go back.
- **Choose your kind of business when you first open Hoard.** Admins pick it from a list, and Hoard starts with the units, categories, lot details and waste reasons it uses; "Something else" starts blank. Tea, coffee and drinks adds waste by reason, planned against actual, and an allergen matrix to Reports; Bakery and food, Cosmetics and soap, and Restaurant, café and bar come with their own picking rules and hold reasons.
- **Stock, Production and Reports pages in each initiative Hoard is placed in, and Settings for community admins.** Hoard keeps one set of stock for the whole community, whichever initiative it's opened from.
- **Materials, products and variations,** with SKUs, categories, purchase and tracking units, low-stock limits and product pictures. Each record page shows stock and lots per location, and who added and last changed it.
- **Purchases from your community's contacts.** Receiving a line makes its lot and moves the material's average cost. Discount and shipping are spread over the lines by value, and tax is kept out. Add a new supplier from the picker without leaving the purchase.
- **Orders and shipments.** Take orders from your business customers, picked from your community's contacts, on an Orders page of their own. Send an order in one shipment or several, with its carrier and tracking number; each line leaves the lot you pick, or the one that expires first. Record what comes back: restocked into its lot, or written off as returned damaged.
- **Connect your Shopify store.** The store's owner makes an app for their store in Shopify, and a community admin saves its details under Hoard's connections in Initiative. Hoard checks the store as it connects, and Settings says why when it can't.
- **Your store's products, matched and edited live.** Each store product is linked to what it sells in Hoard by SKU, then title, or by hand. Edit its title, description, status and images from Hoard straight into the store, or add a product to the store as a draft. Hoard tells the store what is available at each location you map, keeps it in step as stock moves, and keeps a log of what it sent.
- **Store orders copied in, and made to order as they sell.** Paid orders hold their stock, bundles split into their parts, and the store's fulfillments become shipments with their lots. Connecting brings in the last 60 days of orders without moving stock, except those still waiting to be sent. Auto-manufacture makes what a paid order sells from its recipe, such as drinks made fresh: always, or only when it is out of stock, for every product or one at a time.
- **Cost of goods, mass balance and certified volume.** Three reports: what the goods cost over a period, as a tax return asks for it; what each item had, took in, made, used, sold and lost, split by a lot property if you like; and how much certified stock, such as fair trade, came in against how much was sold.
- **Adjustments and stocktakes.** Record a change with a reason, or count materials or products (all, a sample, or one category) and confirm each row. Each stocktake shows its accuracy.
- **Reports:** what to reorder and what your stock is worth, plus CSV and JSON exports of every record.
- **Widgets** for stock to reorder, stock value, waste by reason, planned against actual and the allergen matrix, and a **Stock overview dashboard.**
- **Your own properties and layouts.** Admins add properties to materials, products and lots, and choose which fields each list and record page shows. Everyone fills them in.
- **Settings** for units, categories, locations and the contact holding each one's address, adjustment and hold reasons, and your currency.
- **"See costs and prices",** a permission your community's roles hold. Without it, costs and prices stay hidden.

