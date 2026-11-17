The `Inventory` class in the `stock` package can add items but not take them out. Add a method `remove(sku: str, quantity: int) -> int` that removes `quantity` items of `sku` and returns how many are left.

- `quantity` must be a positive int, as in `add`; otherwise raise `ValueError`.
- Removing more than is in stock (including a SKU that is not there at all) raises `ValueError` and leaves the stock unchanged.
- When the count of a SKU reaches zero, the SKU disappears from `skus()`.
