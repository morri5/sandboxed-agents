from confmerge import merge


def test_nested_merge() -> None:
    base = {"db": {"host": "localhost", "port": 5432}, "debug": False}
    assert merge(base, {"db": {"port": 6543}}) == {
        "db": {"host": "localhost", "port": 6543},
        "debug": False,
    }


def test_three_levels() -> None:
    base = {"a": {"b": {"c": 1, "d": 2}, "e": 3}}
    assert merge(base, {"a": {"b": {"d": 20, "f": 30}}}) == {
        "a": {"b": {"c": 1, "d": 20, "f": 30}, "e": 3}
    }


def test_arguments_are_not_modified() -> None:
    base = {"x": {"y": 1}, "z": [1]}
    override = {"x": {"w": 2}, "z": [2]}
    merge(base, override)
    assert base == {"x": {"y": 1}, "z": [1]}
    assert override == {"x": {"w": 2}, "z": [2]}


def test_result_shares_no_dict_with_the_arguments() -> None:
    base = {"x": {"y": {"k": 1}}}
    override = {"n": {"m": 2}}
    result = merge(base, override)
    result["x"]["y"]["k"] = 99
    result["n"]["m"] = 99
    assert base == {"x": {"y": {"k": 1}}}
    assert override == {"n": {"m": 2}}


def test_lists_are_replaced() -> None:
    assert merge({"tags": ["a", "b"]}, {"tags": ["c"]}) == {"tags": ["c"]}


def test_dict_and_scalar_replace_each_other() -> None:
    assert merge({"v": {"a": 1}}, {"v": 5}) == {"v": 5}
    assert merge({"v": 5}, {"v": {"a": 1}}) == {"v": {"a": 1}}
