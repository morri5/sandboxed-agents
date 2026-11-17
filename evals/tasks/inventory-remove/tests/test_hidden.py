import pytest

from stock import Inventory


def test_remove_returns_what_is_left() -> None:
    inv = Inventory()
    inv.add("bolt", 10)
    assert inv.remove("bolt", 4) == 6
    assert inv.count("bolt") == 6


def test_removing_everything_drops_the_sku() -> None:
    inv = Inventory()
    inv.add("nut", 2)
    inv.add("washer", 1)
    assert inv.remove("nut", 2) == 0
    assert inv.skus() == ["washer"]
    assert inv.count("nut") == 0


def test_removing_too_many_changes_nothing() -> None:
    inv = Inventory()
    inv.add("gear", 3)
    with pytest.raises(ValueError):
        inv.remove("gear", 4)
    assert inv.count("gear") == 3
    assert inv.skus() == ["gear"]


def test_unknown_sku() -> None:
    inv = Inventory()
    with pytest.raises(ValueError):
        inv.remove("ghost", 1)
    assert inv.skus() == []


@pytest.mark.parametrize("quantity", [0, -1])
def test_quantity_must_be_positive(quantity: int) -> None:
    inv = Inventory()
    inv.add("cog", 5)
    with pytest.raises(ValueError):
        inv.remove("cog", quantity)
    assert inv.count("cog") == 5


def test_add_still_validates() -> None:
    with pytest.raises(ValueError):
        Inventory().add("cog", 0)
