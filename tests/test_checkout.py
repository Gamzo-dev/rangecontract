import pytest

from rangecontract._checkout import Checkout, CheckoutError, _component_key

OID = "a" * 40


def tree_inventory(paths, *, mode_type="100644 blob", size="1"):
    return b"".join(f"{mode_type} {OID} {size}\t{path}\0".encode() for path in paths)


def test_component_key_decomposes_before_and_after_casefolding():
    expected = "\u03b1\u0301\u03b9.txt"
    assert _component_key("\u03b1\u0345\u0301.txt") == expected
    assert _component_key("\u03b1\u0301\u0345.txt") == expected


@pytest.mark.parametrize(("left", "right"), [
    ("\u03b1\u0345\u0301.txt", "\u03b1\u0301\u0345.txt"),
    ("Caf\u00e9.txt", "Cafe\u0301.txt"),
    ("ONE.txt", "one.txt"),
    ("Stra\u00dfe.txt", "STRASSE.txt"),
])
def test_tree_rejects_canonical_caseless_file_collisions(left, right):
    with pytest.raises(CheckoutError, match="validation tree path collision"):
        Checkout.from_tree(tree_inventory((left, right)), OID)


def test_tree_rejects_canonical_caseless_directory_collisions():
    data = tree_inventory(("src", "src/Caf\u00e9", "src/Cafe\u0301"), mode_type="040000 tree", size="-")
    with pytest.raises(CheckoutError, match="validation tree path collision"):
        Checkout.from_tree(data, OID)


@pytest.mark.parametrize(("left", "right"), [
    ("A.txt", "\uff21.txt"),
    ("a.txt", "\u0430.txt"),
    ("a\u0300.txt", "a\u0301.txt"),
])
def test_tree_preserves_distinct_unicode_spellings(left, right):
    data = tree_inventory((left, right))
    assert Checkout.from_tree(data, OID).entries.keys() == {left, right}
