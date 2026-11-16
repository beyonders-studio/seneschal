"""The Admin GraphQL documents the Shopify connector sends.

Each page size keeps a call's priced cost under the store's limit for one
call; a connection longer than its page is read on with the matching
``…_PAGE`` document.
"""

from __future__ import annotations

PAGE_INFO = "pageInfo { hasNextPage endCursor }"

SHOP = "query HoardShop { shop { name myshopifyDomain } }"

# Webhook subscriptions.

WEBHOOKS = f"""
query HoardWebhooks($uri: String!, $after: String) {{
  webhookSubscriptions(first: 100, uri: $uri, after: $after) {{
    nodes {{ id topic }}
    {PAGE_INFO}
  }}
}}
"""

WEBHOOK_CREATE = """
mutation HoardWebhookCreate($topic: WebhookSubscriptionTopic!, $input: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $input) {
    webhookSubscription { id }
    userErrors { field message }
  }
}
"""

WEBHOOK_DELETE = """
mutation HoardWebhookDelete($id: ID!) {
  webhookSubscriptionDelete(id: $id) {
    deletedWebhookSubscriptionId
    userErrors { field message }
  }
}
"""

# Products.

VARIANT_FIELDS = "id title sku price selectedOptions { name value } inventoryItem { id }"

#: What a product is, besides its variants, images and sales channels.
PRODUCT_OWN_FIELDS = (
    "id title descriptionHtml status updatedAt productType vendor tags options { name }"
)

PRODUCT_FIELDS = f"""
{PRODUCT_OWN_FIELDS}
variants(first: 50) {{ nodes {{ {VARIANT_FIELDS} }} {PAGE_INFO} }}
media(first: 10) {{ nodes {{ id alt ... on MediaImage {{ image {{ url }} }} }} }}
resourcePublicationsV2(first: 10, onlyPublished: true) {{ nodes {{ publication {{ id }} }} }}
"""

PRODUCTS = f"""
query HoardProducts($query: String, $after: String) {{
  products(first: 4, after: $after, query: $query, sortKey: UPDATED_AT) {{
    nodes {{ {PRODUCT_FIELDS} }}
    {PAGE_INFO}
  }}
}}
"""

PRODUCT = f"""
query HoardProduct($id: ID!) {{
  product(id: $id) {{ {PRODUCT_FIELDS} }}
}}
"""

VARIANTS_PAGE = f"""
query HoardVariantsPage($id: ID!, $after: String) {{
  product(id: $id) {{
    variants(first: 100, after: $after) {{ nodes {{ {VARIANT_FIELDS} }} {PAGE_INFO} }}
  }}
}}
"""

PRODUCT_CREATE = """
mutation HoardProductCreate($product: ProductCreateInput!, $media: [CreateMediaInput!]) {
  productCreate(product: $product, media: $media) {
    product { id variants(first: 1) { nodes { id } } }
    userErrors { field message }
  }
}
"""

PRODUCT_UPDATE = """
mutation HoardProductUpdate($product: ProductUpdateInput!, $media: [CreateMediaInput!]) {
  productUpdate(product: $product, media: $media) {
    product { id }
    userErrors { field message }
  }
}
"""

VARIANTS_CREATE = """
mutation HoardVariantsCreate(
  $productId: ID!, $variants: [ProductVariantsBulkInput!]!, $strategy: ProductVariantsBulkCreateStrategy
) {
  productVariantsBulkCreate(productId: $productId, variants: $variants, strategy: $strategy) {
    productVariants { id }
    userErrors { field message }
  }
}
"""

VARIANTS_UPDATE = """
mutation HoardVariantsUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) {
    productVariants { id }
    userErrors { field message }
  }
}
"""

VARIANTS_DELETE = """
mutation HoardVariantsDelete($productId: ID!, $variantsIds: [ID!]!) {
  productVariantsBulkDelete(productId: $productId, variantsIds: $variantsIds) {
    userErrors { field message }
  }
}
"""

FILES_UPDATE = """
mutation HoardFilesUpdate($files: [FileUpdateInput!]!) {
  fileUpdate(files: $files) {
    files { id }
    userErrors { field message }
  }
}
"""

PUBLISH = """
mutation HoardPublish($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) {
    userErrors { field message }
  }
}
"""

UNPUBLISH = """
mutation HoardUnpublish($id: ID!, $input: [PublicationInput!]!) {
  publishableUnpublish(id: $id, input: $input) {
    userErrors { field message }
  }
}
"""

# Fields: metafield definitions, the entries a field picks from, and values.

FIELD_DEFINITIONS = f"""
query HoardFieldDefinitions($ownerType: MetafieldOwnerType!, $after: String) {{
  metafieldDefinitions(ownerType: $ownerType, first: 100, after: $after) {{
    nodes {{ name namespace key type {{ name }} validations {{ name value }} metafieldsCount }}
    {PAGE_INFO}
  }}
}}
"""

FIELD_ENTRIES = f"""
query HoardFieldEntries($id: ID!, $after: String) {{
  metaobjectDefinition(id: $id) {{
    metaobjects(first: 250, after: $after) {{ nodes {{ id displayName }} {PAGE_INFO} }}
  }}
}}
"""

FIELD_ENTRIES_BY_TYPE = f"""
query HoardFieldEntriesByType($type: String!, $after: String) {{
  metaobjects(type: $type, first: 250, after: $after) {{ nodes {{ id displayName }} {PAGE_INFO} }}
}}
"""

FIELD_VALUE_FIELDS = "namespace key value"

#: One product's values of the fields named, with its first variants'.
FIELD_VALUES = f"""
query HoardFieldValues($id: ID!, $keys: [String!], $count: Int!, $variants: Int!) {{
  product(id: $id) {{
    id
    metafields(first: $count, keys: $keys) {{ nodes {{ {FIELD_VALUE_FIELDS} }} }}
    variants(first: $variants) {{
      nodes {{ id metafields(first: $count, keys: $keys) {{ nodes {{ {FIELD_VALUE_FIELDS} }} }} }}
    }}
  }}
}}
"""

FIELDS_SET = """
mutation HoardFieldsSet($metafields: [MetafieldsSetInput!]!) {
  metafieldsSet(metafields: $metafields) {
    metafields { namespace key type value owner { ... on Product { id } ... on ProductVariant { id } } }
    userErrors { field message code }
  }
}
"""

FIELDS_DELETE = """
mutation HoardFieldsDelete($metafields: [MetafieldIdentifierInput!]!) {
  metafieldsDelete(metafields: $metafields) {
    deletedMetafields { ownerId namespace key }
    userErrors { field message }
  }
}
"""

# Locations and stock.

LOCATIONS = f"""
query HoardLocations($after: String) {{
  locations(first: 100, after: $after) {{ nodes {{ id name }} {PAGE_INFO} }}
}}
"""

SET_AVAILABLE = """
mutation HoardSetAvailable($input: InventorySetQuantitiesInput!, $idempotencyKey: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    userErrors { code field message }
  }
}
"""

AVAILABLE = """
query HoardAvailable($item: ID!, $location: ID!) {
  inventoryItem(id: $item) {
    inventoryLevel(locationId: $location) { quantities(names: ["available"]) { name quantity } }
  }
}
"""

# Orders.

# Of the customer, only their id; of the buyer, only whether it is a company.
ORDER_FIELDS = """
id name processedAt updatedAt cancelledAt fullyPaid displayFulfillmentStatus currencyCode
totalShippingPriceSet { shopMoney { amount } }
totalTaxSet { shopMoney { amount } }
totalDiscountsSet { shopMoney { amount } }
totalRefundedSet { shopMoney { amount } }
customer { id }
purchasingEntity { __typename }
"""

LINE_FIELDS = """
id name sku quantity currentQuantity
originalUnitPriceSet { shopMoney { amount } }
product { id }
variant { id }
lineItemGroup { id }
sellingPlan { sellingPlanId }
"""

ORDER_IDS = f"""
query HoardOrderIds($query: String, $after: String) {{
  orders(first: 100, after: $after, query: $query, sortKey: UPDATED_AT) {{
    nodes {{ id }}
    {PAGE_INFO}
  }}
}}
"""

ORDER = f"""
query HoardOrder($id: ID!) {{
  order(id: $id) {{
    {ORDER_FIELDS}
    lineItems(first: 50) {{ nodes {{ {LINE_FIELDS} }} {PAGE_INFO} }}
  }}
}}
"""

LINES_PAGE = f"""
query HoardLinesPage($id: ID!, $after: String) {{
  order(id: $id) {{
    lineItems(first: 100, after: $after) {{ nodes {{ {LINE_FIELDS} }} {PAGE_INFO} }}
  }}
}}
"""

REFUND_LINE_FIELDS = "quantity lineItem { id }"

REFUNDS = f"""
query HoardRefunds($id: ID!) {{
  order(id: $id) {{
    refunds(first: 50) {{
      id
      refundLineItems(first: 50) {{ nodes {{ {REFUND_LINE_FIELDS} }} {PAGE_INFO} }}
    }}
  }}
}}
"""

REFUND_LINES_PAGE = f"""
query HoardRefundLinesPage($id: ID!, $after: String) {{
  refund(id: $id) {{
    refundLineItems(first: 100, after: $after) {{ nodes {{ {REFUND_LINE_FIELDS} }} {PAGE_INFO} }}
  }}
}}
"""

FULFILLMENT_LINE_FIELDS = "quantity lineItem { id }"

FULFILLMENTS = f"""
query HoardFulfillments($id: ID!) {{
  order(id: $id) {{
    fulfillments(first: 8) {{
      id status createdAt
      location {{ id }}
      trackingInfo(first: 1) {{ number company url }}
      fulfillmentLineItems(first: 50) {{ nodes {{ {FULFILLMENT_LINE_FIELDS} }} {PAGE_INFO} }}
    }}
  }}
}}
"""

FULFILLMENT_LINES_PAGE = f"""
query HoardFulfillmentLinesPage($id: ID!, $after: String) {{
  fulfillment(id: $id) {{
    fulfillmentLineItems(first: 100, after: $after) {{
      nodes {{ {FULFILLMENT_LINE_FIELDS} }}
      {PAGE_INFO}
    }}
  }}
}}
"""

FULFILLMENT_ORDER_LINE_FIELDS = "id remainingQuantity lineItem { id }"

FULFILLMENT_ORDERS = f"""
query HoardFulfillmentOrders($id: ID!) {{
  order(id: $id) {{
    fulfillmentOrders(first: 8) {{
      nodes {{
        id status fulfillAt
        assignedLocation {{ location {{ id }} }}
        lineItems(first: 50) {{ nodes {{ {FULFILLMENT_ORDER_LINE_FIELDS} }} {PAGE_INFO} }}
      }}
    }}
  }}
}}
"""

FULFILLMENT_ORDER_LINES_PAGE = f"""
query HoardFulfillmentOrderLinesPage($id: ID!, $after: String) {{
  fulfillmentOrder(id: $id) {{
    lineItems(first: 100, after: $after) {{
      nodes {{ {FULFILLMENT_ORDER_LINE_FIELDS} }}
      {PAGE_INFO}
    }}
  }}
}}
"""

FULFILLMENT_CREATE = """
mutation HoardFulfillmentCreate($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment { id }
    userErrors { field message }
  }
}
"""

# Customer segments, and whether customers are in one.

SEGMENTS = f"""
query HoardSegments($after: String) {{
  segments(first: 100, after: $after) {{ nodes {{ id name }} {PAGE_INFO} }}
}}
"""


def segment_membership(count: int) -> str:
    """Whether each of ``count`` customers, ``$c0`` on, is in the segments
    ``$segmentIds`` names, each answered under its own alias."""
    customers = ", ".join(f"$c{at}: ID!" for at in range(count))
    asked = "\n".join(
        f"  c{at}: customerSegmentMembership(customerId: $c{at}, segmentIds: $segmentIds) "
        "{ memberships { segmentId isMember } }"
        for at in range(count)
    )
    return f"query HoardSegmentMembership($segmentIds: [ID!]!, {customers}) {{\n{asked}\n}}"


# The history import, in bulk.

BULK_RUN = """
mutation HoardBulkRun($query: String!) {
  bulkOperationRunQuery(query: $query) {
    bulkOperation { id status }
    userErrors { field message }
  }
}
"""

BULK_STATUS = """
query HoardBulkStatus($id: ID!) {
  bulkOperation(id: $id) { id status errorCode url }
}
"""


def bulk_orders(search: str) -> str:
    """A bulk query for the orders ``search`` finds, with their lines."""
    return f"""
{{
  orders(query: "{search}") {{
    edges {{
      node {{
        {ORDER_FIELDS}
        lineItems {{ edges {{ node {{ {LINE_FIELDS} }} }} }}
      }}
    }}
  }}
}}
"""


def bulk_products() -> str:
    """A bulk query for every product, with its variants, images and the sales
    channels showing it, each nested row naming its type."""
    return f"""
{{
  products {{
    edges {{
      node {{
        {PRODUCT_OWN_FIELDS}
        variants {{ edges {{ node {{ __typename {VARIANT_FIELDS} }} }} }}
        media {{ edges {{ node {{ __typename id alt ... on MediaImage {{ image {{ url }} }} }} }} }}
        resourcePublicationsV2(onlyPublished: true) {{
          edges {{ node {{ __typename publication {{ id }} }} }}
        }}
      }}
    }}
  }}
}}
"""


def bulk_field_values(variants: bool) -> str:
    """A bulk query for every product's fields' values, and, with
    ``variants``, its variants'."""
    metafields = f"metafields {{ edges {{ node {{ __typename {FIELD_VALUE_FIELDS} }} }} }}"
    of_variants = (
        f"variants {{ edges {{ node {{ __typename id {metafields} }} }} }}" if variants else ""
    )
    return f"""
{{
  products {{
    edges {{
      node {{
        id
        {metafields}
        {of_variants}
      }}
    }}
  }}
}}
"""


def bulk_stock() -> str:
    """A bulk query for every inventory item's cost, and what is available of
    it at each location."""
    return """
{
  inventoryItems {
    edges {
      node {
        id
        unitCost { amount }
        inventoryLevels {
          edges { node { location { id } quantities(names: ["available"]) { name quantity } } }
        }
      }
    }
  }
}
"""
