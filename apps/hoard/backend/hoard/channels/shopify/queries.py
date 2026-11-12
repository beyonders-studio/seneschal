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

PRODUCT_FIELDS = f"""
id title descriptionHtml status updatedAt
options {{ name }}
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

ORDER_FIELDS = """
id name processedAt updatedAt cancelledAt fullyPaid displayFulfillmentStatus currencyCode
totalShippingPriceSet { shopMoney { amount } }
totalTaxSet { shopMoney { amount } }
totalDiscountsSet { shopMoney { amount } }
"""

LINE_FIELDS = """
id name sku currentQuantity
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
