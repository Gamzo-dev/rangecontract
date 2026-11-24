import pytest

from rangecontract._contract import ContractError, Glob, parse_contract
from rangecontract._git import GitError, parse_name_status

VALID = (
    b'base_commit = "1234567890123456789012345678901234567890"\n'
    b'allowed_paths = []\n'
    b'forbidden_paths = []\n'
    b'max_changed_files = 0\n'
)


@pytest.mark.parametrize(("pattern", "path", "expected"), [
    ("src/**/*.py", "src/main.py", True),
    ("src/**/*.py", "src/a/b/main.py", True),
    ("src/*.py", "src/a/main.py", False),
    ("src/**", "src", True),
    ("**/x/**/y", "x/y", True),
    ("**/x/**/y", "a/x/b/y", True),
    ("**/x/**/y", "a/x/b/y/z", False),
    ("*.txt", "a/.hidden.txt", False),
    ("*.txt", ".hidden.txt", True),
    ("a?b", "a\u2603b", True),
    ("a?b", "a\u0065\u0301b", False),
    ("A/**", "a/content", False),
    ("[a].txt", "[a].txt", True),
    ("[a].txt", "a.txt", False),
    (r"a\*b\?c\\d", "a*b?c\\d", True),
    ("**/*.txt", "a\nb.txt", True),
])
def test_glob_semantics(pattern, path, expected):
    assert Glob.parse(pattern).matches(path) is expected


@pytest.mark.parametrize("pattern", ["", "/a", "a/", "a//b", "../a", "./a", "a**b", "***", r"a\x", "a\\"])
def test_invalid_globs(pattern):
    with pytest.raises(ContractError):
        Glob.parse(pattern)


@pytest.mark.parametrize("contents", [
    VALID + b"unknown = 1\n",
    VALID + b"max_changed_files = 1\n",
    VALID.replace(b"max_changed_files = 0", b"max_changed_files = -1"),
    VALID.replace(b"max_changed_files = 0", b"max_changed_files = true"),
    VALID.replace(b"1234567890123456789012345678901234567890", b"main"),
    VALID.replace(b"allowed_paths = []", b"allowed_paths = [1]"),
    VALID.replace(b"forbidden_paths = []", b'forbidden_paths = ["a/**x"]'),
    VALID + b"[[checks]]\nname = 'x'\nargv = 'echo hi'\n",
    VALID + b"[[checks]]\nname = 'x'\nargv = ['']\n",
    VALID + b"[[checks]]\nname = 'x'\nargv = ['a']\ntimeout_seconds = true\n",
    VALID + b"[[checks]]\nname = 'x'\nargv = ['a']\ntimeout_seconds = 601\n",
    VALID + b"[[checks]]\nname = 'x'\nargv = ['a']\n[[checks]]\nname = 'x'\nargv = ['b']\n",
    VALID + b"[[checks]]\nname = 'x'\nargv = ['a']\nshell = true\n",
    VALID + b"\xff",
])
def test_invalid_contracts(contents):
    with pytest.raises(ContractError):
        parse_contract(contents)


@pytest.mark.parametrize("output", [
    b"M\0file", b"M\0", b"M\0\0", b"M\0/file\0", b"M\0../file\0",
    b"M\0non-utf8-\xff\0", b"T\0file\0", b"R\0a\0b\0", b"C101\0a\0b\0",
    b"R100\0a\0", b"C100\0a\0a\0",
])
def test_malformed_or_unsupported_name_status(output):
    with pytest.raises(GitError):
        parse_name_status(output)
