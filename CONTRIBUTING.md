# Contributing

Use Python 3.11+ and Git 2.42+. Create an isolated development environment, then run these commands from the repository root.

```sh
python -I -m pip install ".[dev]"
python -I -m pytest
python -m build
python -m twine check --strict dist/*
```

The tests create temporary repositories using real Git commands. For changes to scope decisions, add a regression case with the smallest commit range that reproduces the behavior. Describe contract schema, exit code or report changes in the README and changelog.

Keep the runtime dependency-free. Keep test fixtures local and avoid using credentials or network services in tests.
