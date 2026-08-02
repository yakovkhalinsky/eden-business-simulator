# Ecommerce simulator

Slug: `ecommerce`  
Source module: [`src/eden_business_simulator/businesses/ecommerce.py`](../../src/eden_business_simulator/businesses/ecommerce.py)

## Domain overview

The ecommerce simulator models a small online store. Customers browse products, update carts, place orders, pay, receive shipments, and occasionally request refunds. Inventory levels move in response to orders and corrections.

## Systems represented

- Customer account registry
- Product catalog with SKUs, prices, and inventory
- Shopping cart
- Order management
- Payment processing
- Shipping and carriers
- Inventory ledger
- Refunds

## Operational workflow

A typical ecommerce run follows this lifecycle:

1. Seed an initial customer base and product catalog.
2. Customers view products and add or remove items from carts.
3. Carts convert into placed orders.
4. Payments are processed (approved or declined).
5. Approved orders are shipped with a carrier and tracking number.
6. Inventory is adjusted periodically due to orders or corrections.
7. Some orders receive refunds.

## Event catalog

### `customer_created`

A new shopper registers an account.

```json
{
  "customer_id": "cust_0005",
  "name": "Eric Cooper",
  "email": "jonathanking@example.net",
  "registered_at": "2026-08-02T13:21:11.800334+00:00"
}
```

### `product_viewed`

A customer opens a product page.

```json
{
  "customer_id": "cust_0001",
  "product_id": "prod_0007",
  "viewed_at": "2026-08-02T13:21:11.796917+00:00"
}
```

### `cart_updated`

Items are added to or changed in a cart.

```json
{
  "customer_id": "cust_0002",
  "items": [
    {
      "product_id": "prod_0000",
      "quantity": 2
    }
  ],
  "updated_at": "2026-08-02T13:21:11.896917+00:00"
}
```

### `order_placed`

A cart becomes an order.

```json
{
  "order_id": "ord_000001",
  "customer_id": "cust_0000",
  "items": [
    {
      "product_id": "prod_0005",
      "sku": "SKU-8377",
      "quantity": 3,
      "unit_price": 56.44,
      "line_total": 169.32
    }
  ],
  "placed_at": "2026-08-02T13:21:12.096917+00:00",
  "total": 169.32,
  "currency": "GBP"
}
```

### `payment_processed`

A payment attempt is approved or declined.

```json
{
  "order_id": "ord_000001",
  "payment_id": "pay_000001",
  "amount": 169.32,
  "currency": "GBP",
  "status": "approved",
  "processed_at": "2026-08-02T13:21:12.196917+00:00"
}
```

### `order_shipped`

An approved order leaves the warehouse.

```json
{
  "order_id": "ord_000003",
  "shipped_at": "2026-08-02T13:21:15.096917+00:00",
  "carrier": "DHL",
  "tracking_number": "TRK-2786838738"
}
```

### `inventory_adjusted`

Stock levels move due to sales, corrections, or restocking.

```json
{
  "product_id": "prod_0015",
  "sku": "SKU-8836",
  "delta": -5,
  "new_inventory": 366,
  "reason": "correction",
  "adjusted_at": "2026-08-02T13:21:12.996917+00:00"
}
```

### `refund_issued`

A refund is issued for an order.

```json
{
  "order_id": "ord_000003",
  "amount": 134.98,
  "currency": "AUD",
  "reason": "defective",
  "issued_at": "2026-08-02T13:21:15.596917+00:00"
}
```

## Configuration notes

- Override the initial customer count with `initial_state_overrides.initial_customers` (default `5`).
- The simulator creates `20` initial products with random SKUs, prices, and inventory levels.
- Supported currencies: `USD`, `EUR`, `GBP`, `AUD`, `CAD`.
- Carriers: `FedEx`, `UPS`, `DHL`, `USPS`.
- A declined payment leaves the order in a `payment_failed` state.
- Event weights are state-dependent; for example, `order_placed` is only chosen when at least one cart has items.

## CLI quick start

```bash
# 60 simulated seconds at 2 events per second
uv run eden-business-simulator run ecommerce --duration 60 --rate 2 --seed 42 --no-realtime

# Pipe into an evaluator
uv run eden-business-simulator run ecommerce --duration 30 --rate 5 --seed 42 --no-realtime | ./my-evaluator

# Persist to SQLite and replay later
uv run eden-business-simulator daemon ecommerce \
  --stream-id ecommerce_seed42 \
  --storage sqlite --storage-uri ecommerce.db \
  --duration 120 --rate 2 --no-realtime

uv run eden-business-simulator replay ecommerce_seed42 \
  --storage sqlite --storage-uri ecommerce.db --speed 1.0
```
