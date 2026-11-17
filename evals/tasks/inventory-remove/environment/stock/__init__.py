"""Track item quantities by SKU."""


class Inventory:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def add(self, sku: str, quantity: int) -> None:
        """Add `quantity` (a positive int) items of `sku`."""
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        self._counts[sku] = self._counts.get(sku, 0) + quantity

    def count(self, sku: str) -> int:
        return self._counts.get(sku, 0)

    def skus(self) -> list[str]:
        """The SKUs in stock, sorted."""
        return sorted(self._counts)
