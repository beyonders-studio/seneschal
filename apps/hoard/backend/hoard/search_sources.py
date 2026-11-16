"""What Hoard's search finds (``seneschal_core.search``): one source per kind
of record people look for, each with the page that opens it.

* **Materials**: name; SKU and supplier code; ingredient name, notes and
  text properties. Archived materials are archived.
* **Bases**: name; SKU; notes and text properties. Archived bases are archived.
* **Products**: name; their variations' SKUs; the variations' attributes, the
  title, type, vendor and tags of each store's copy of them, and text
  properties. Archived products are archived.
* **Lots**: number, which is their code too; their item's name, notes and
  text properties.
* **Production runs**: code; their item's name.
* **Purchases**: code; their materials' names and notes.
* **Orders**: code, the store's number for them and for their customer; their
  lines' items' names.
* **Stocktakes**: their location and date; notes.
* **Holds**: their lot's number, which is their code too; reason and notes.
  Ended holds are archived. A hold opens its lot.

Costs and prices are never indexed. A change to anything a source reads, on
its own table or another's, indexes the record again.
"""

from __future__ import annotations

from seneschal_core.search import SearchDependency, SearchSource


def _item_name(item: str) -> str:
    """What the item ``item`` names is called, as ``hoard.events.item_name``
    calls it: a material's or a base's name, or a variation's product's name
    with its attributes."""
    return f"""coalesce(
        (SELECT m.name FROM materials m WHERE m.id = {item}),
        (SELECT b.name FROM bases b WHERE b.id = {item}),
        (SELECT concat_ws(' ', p.name, (
                    SELECT string_agg(a.value, ' ') FROM jsonb_each_text(v.attributes) a
                ))
           FROM variations v JOIN products p ON p.id = v.product_id
          WHERE v.id = {item})
    )"""


def _item_names(table: str, record: str, item: str) -> tuple[SearchDependency, ...]:
    """The dependencies of a source naming the items ``table``'s rows hold:
    each row's ``record`` is the source's record, and its ``item`` the item."""
    rows = f"SELECT s.{record} FROM {table} s WHERE s.{item}"
    return (
        SearchDependency("materials", f"{rows} = d.id", watch=("name",)),
        SearchDependency("bases", f"{rows} = d.id", watch=("name",)),
        SearchDependency("variations", f"{rows} = d.id", watch=("attributes", "product_id")),
        SearchDependency(
            "products",
            f"{rows} IN (SELECT v.id FROM variations v WHERE v.product_id = d.id)",
            watch=("name",),
        ),
    )


def _text_properties(column: str) -> str:
    """The values of the record's text properties, as ``property_values`` keeps
    them under ``column``."""
    return f"""(SELECT string_agg(pv.value #>> '{{}}', ' ' ORDER BY pv.id)
         FROM property_values pv JOIN property_definitions pd ON pd.id = pv.definition_id
        WHERE pv.{column} = r.id AND pd.type = 'text')"""


def _properties(column: str) -> SearchDependency:
    return SearchDependency("property_values", f"SELECT d.{column}")


MATERIALS = SearchSource(
    kind="material",
    table="materials",
    title="r.name",
    path="'/stock/materials/' || r.id",
    codes="ARRAY[r.sku, r.supplier_code]",
    body=("r.ingredient_name", "r.notes", _text_properties("material_id")),
    archived="r.status = 'archived'",
    watch=("name", "sku", "supplier_code", "ingredient_name", "notes", "status"),
    dependencies=(_properties("material_id"),),
)

BASES = SearchSource(
    kind="base",
    table="bases",
    title="r.name",
    path="'/stock/bases/' || r.id",
    codes="ARRAY[r.sku]",
    body=("r.notes", _text_properties("base_id")),
    archived="r.status = 'archived'",
    watch=("name", "sku", "notes", "status"),
    dependencies=(_properties("base_id"),),
)

#: Each store's copy of the product ``r``: those its variations are listed as.
_STORE_COPIES = """(SELECT string_agg(
        concat_ws(' ', sp.title, sp.product_type, sp.vendor, array_to_string(sp.tags, ' ')),
        ' ' ORDER BY sp.id)
      FROM store_products sp
     WHERE EXISTS (
           SELECT 1 FROM listings li JOIN variations v ON v.id = li.item_id
            WHERE v.product_id = r.id AND li.store_id = sp.store_id
              AND li.product_id = sp.product_id
     ))"""

PRODUCTS = SearchSource(
    kind="product",
    table="products",
    title="r.name",
    path="'/stock/products/' || r.id",
    codes="ARRAY(SELECT v.sku FROM variations v WHERE v.product_id = r.id ORDER BY v.id)",
    body=(
        """(SELECT string_agg(a.value, ' ' ORDER BY v.id)
             FROM variations v, jsonb_each_text(v.attributes) a WHERE v.product_id = r.id)""",
        _STORE_COPIES,
        _text_properties("product_id"),
    ),
    archived="r.status = 'archived'",
    watch=("name", "status"),
    dependencies=(
        SearchDependency(
            "variations", "SELECT d.product_id", watch=("product_id", "sku", "attributes")
        ),
        SearchDependency(
            "listings",
            "SELECT v.product_id FROM variations v WHERE v.id = d.item_id",
            watch=("item_id", "store_id", "product_id"),
        ),
        SearchDependency(
            "store_products",
            """SELECT v.product_id FROM listings li JOIN variations v ON v.id = li.item_id
                WHERE li.store_id = d.store_id AND li.product_id = d.product_id""",
            watch=("store_id", "product_id", "title", "product_type", "vendor", "tags"),
        ),
        _properties("product_id"),
    ),
)

LOTS = SearchSource(
    kind="lot",
    table="lots",
    title="r.number",
    path="'/stock/lots/' || r.id",
    codes="ARRAY[r.number]",
    subtitle=_item_name("r.item_id"),
    body=(_item_name("r.item_id"), "r.notes", _text_properties("lot_id")),
    watch=("number", "item_id", "notes"),
    dependencies=(*_item_names("lots", "id", "item_id"), _properties("lot_id")),
)

RUNS = SearchSource(
    kind="run",
    table="production_runs",
    title="r.code",
    path="'/production/runs/' || r.id",
    codes="ARRAY[r.code]",
    subtitle=_item_name("r.item_id"),
    body=(_item_name("r.item_id"),),
    watch=("code", "item_id"),
    dependencies=_item_names("production_runs", "id", "item_id"),
)

PURCHASES = SearchSource(
    kind="purchase",
    table="purchases",
    title="r.code",
    path="'/stock/purchases/' || r.id",
    codes="ARRAY[r.code]",
    body=(
        """(SELECT string_agg(m.name, ' ' ORDER BY pl.id)
             FROM purchase_lines pl JOIN materials m ON m.id = pl.material_id
            WHERE pl.purchase_id = r.id)""",
        "r.notes",
    ),
    watch=("code", "notes"),
    dependencies=(
        SearchDependency(
            "purchase_lines", "SELECT d.purchase_id", watch=("purchase_id", "material_id")
        ),
        SearchDependency(
            "materials",
            "SELECT pl.purchase_id FROM purchase_lines pl WHERE pl.material_id = d.id",
            watch=("name",),
        ),
    ),
)

ORDERS = SearchSource(
    kind="order",
    table="orders",
    title="r.code",
    path="'/orders/' || r.id",
    codes="ARRAY[r.code, r.external_number, r.store_customer]",
    body=(
        f"""(SELECT string_agg({_item_name("ol.item_id")}, ' ' ORDER BY ol.id)
              FROM order_lines ol WHERE ol.order_id = r.id)""",
    ),
    watch=("code", "external_number", "store_customer"),
    dependencies=(
        SearchDependency("order_lines", "SELECT d.order_id", watch=("order_id", "item_id")),
        *_item_names("order_lines", "order_id", "item_id"),
    ),
)

STOCKTAKES = SearchSource(
    kind="stocktake",
    table="stocktakes",
    title="""concat_ws(' ',
        (SELECT l.name FROM locations l WHERE l.id = r.location_id),
        to_char(r.dated_on, 'YYYY-MM-DD'))""",
    path="'/stock/stocktakes/' || r.id",
    body=("r.notes",),
    watch=("location_id", "dated_on", "notes"),
    dependencies=(
        SearchDependency(
            "locations",
            "SELECT s.id FROM stocktakes s WHERE s.location_id = d.id",
            watch=("name",),
        ),
    ),
)

HOLDS = SearchSource(
    kind="hold",
    table="holds",
    title="(SELECT l.number FROM lots l WHERE l.id = r.lot_id)",
    path="'/stock/lots/' || r.lot_id",
    codes="ARRAY[(SELECT l.number FROM lots l WHERE l.id = r.lot_id)]",
    body=(
        """(SELECT coalesce(hr.name, replace(hr.key, '_', ' '))
              FROM hold_reasons hr WHERE hr.id = r.reason_id)""",
        "r.notes",
    ),
    archived="r.ended IS NOT NULL",
    watch=("lot_id", "reason_id", "notes", "ended"),
    dependencies=(
        SearchDependency(
            "lots", "SELECT h.id FROM holds h WHERE h.lot_id = d.id", watch=("number",)
        ),
        SearchDependency(
            "hold_reasons",
            "SELECT h.id FROM holds h WHERE h.reason_id = d.id",
            watch=("name", "key"),
        ),
    ),
)


#: Every source.
SOURCES = (
    MATERIALS,
    BASES,
    PRODUCTS,
    LOTS,
    RUNS,
    PURCHASES,
    ORDERS,
    STOCKTAKES,
    HOLDS,
)
