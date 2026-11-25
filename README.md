# RangeContract

RangeContract verifies the net Git tree changes from a pinned base commit to a target commit against a trusted TOML contract. It checks path scope and file count, and can explicitly run required local validation commands.

## Quick start

Use Python 3.11+ and Git 2.42+ on PATH. From a trusted RangeContract source checkout, create a virtual environment in a trusted location outside the checkout you plan to verify. Activate it and install the package with `python -I -m pip install .`. You can also install a trusted wheel with that interpreter. Use a regular installation in the environment's site-packages so Python isolated mode can find it.

```sh
python -I -m rangecontract version
python -I -m rangecontract init --repo /path/to/repo --base main --contract /trusted/change.toml
python -I -m rangecontract verify --repo /path/to/repo --contract /trusted/change.toml --target feature
python -I -m rangecontract verify --repo /path/to/repo --contract /trusted/change.toml --target feature --json
```

Use `python -I -m rangecontract` with the trusted interpreter. Ordinary `python -m rangecontract` searches the current directory and `PYTHONPATH`, so a checkout can supply a module or package that runs before verification starts. Isolated mode excludes those paths and the user site-packages directory. Install in the trusted environment without `--user` and use its absolute interpreter path if PATH is uncertain.

Both commands default to the current directory for `--repo`. Supply the repository root. `init` resolves its base revision to a full commit ID, creates a deny-by-default template, and refuses to overwrite an existing file. Its default output is `.rangecontract.toml` in the current directory. Edit the template before verifying a nonempty range. `verify` requires an explicit contract path. Its target defaults to `HEAD` and can be a ref, tag, or commit ID.

## Contract

See [scope-only.toml](examples/scope-only.toml) and [with-validation.toml](examples/with-validation.toml). Replace the zero placeholder in either example with the full ID of a reviewed base commit.

| Field | Type and rule |
| --- | --- |
| `base_commit` | Full lowercase SHA-1 or SHA-256 ID of a commit object. |
| `allowed_paths` | Array of path patterns. Each affected path must match at least one. An empty array allows no changed paths. |
| `forbidden_paths` | Array of path patterns. A match always blocks a path, including a path that also matches an allowed pattern. |
| `max_changed_files` | Nonnegative integer. One Git change record counts as one file, including each rename and copy. |
| `checks` | Optional array of tables. Each table requires a unique non-blank `name` and an `argv` array of strings with a non-blank executable first. `timeout_seconds` defaults to 60 and accepts integers from 1 to 600. |

The file must be UTF-8 TOML. Unknown fields, malformed patterns, and incorrect types produce an error.

### Path matching

- Patterns match the entire Git path from the repository root. A slash separates components on every platform. Leading, trailing, and repeated slashes and the components `.` and `..` are invalid.
- Matching is case-sensitive and preserves Unicode code points exactly. Dotfiles are ordinary names. There is no filesystem expansion or normalization.
- `*` matches zero or more characters within a component. `?` matches exactly one Unicode code point within a component. A whole `**` component matches zero or more components. Consecutive unescaped stars inside another component are invalid.
- `\*`, `\?` and `\\` match literal asterisks, question marks and backslashes. Other backslash escapes are invalid. Use TOML single-quoted strings for these patterns. Square brackets and braces are ordinary characters.

For example, `src/**/*.py` matches both `src/main.py` and `src/tools/main.py`. `src/*.py` matches only the first. `README.md` matches only the root entry, while `**/README.md` also matches nested entries.

### Range and change records

The base must be an ancestor of the target. The two commits may be identical. Verification compares their trees directly, so intermediate changes that were reverted do not appear in the report. The same resolved commits and Git diff implementation produce the same scope decision from the same contract bytes, independent of working-tree and index changes.

RangeContract uses Git's rename and copy heuristics at a 50% similarity threshold, including copy candidates from unchanged files. Target attributes, ambiguous source candidates, and Git versions can affect classification. RangeContract checks both paths in the records Git reports. Forbidden paths constrain those reported records. Information flow and copy provenance are outside the scope of the policy.

Changed submodule pointers are checked as paths in the outer repository. Unsupported statuses such as file type changes, malformed records, non-UTF-8 changed paths, shallow repositories, grafted history and repository-local diff attribute overrides produce errors. The required Git objects must be available locally. Scope verification also works with a bare repository.

## Required validation

Pass `--run-checks` and supply a trusted contract stored outside the checkout.

```sh
python -I -m rangecontract verify --repo /path/to/repo --contract /trusted/change.toml \
  --target reviewed-commit --run-checks
```

Use a dedicated checkout with `HEAD` and index entries matching the resolved target. RangeContract derives the expected directory and file inventory from the target tree and independently walks the checkout. Each file must be a regular file at the exact Git path. RangeContract computes Git blob IDs from raw bytes and compares them to the tree. Only the root `.git` administrative entry is excluded. Unexpected entries, including ignored files, empty directories, and nested `.git` directories, cause an error.

Validation rejects sibling file or directory names that share a Unicode canonical caseless key. The key is `NFD(casefold(NFD(name)))` (Unicode D145). This conservative rule is independent of each filesystem's equivalence rules. Exact path spelling is still required. [Unicode Standard, Section 3.13.5](https://www.unicode.org/versions/Unicode16.0.0/core-spec/chapter-3/)

Validation also rejects unsupported path names and types, symlinks, junctions, reparse points, submodules, and repository-local Git clean/process filters. Checkout transformations that alter bytes, including CRLF conversion and `ident` expansion, also cause an error. Configure a dedicated checkout to materialize the committed bytes exactly. Disabling `core.autocrlf` alone does not override attributes such as `eol` or `ident`.

RangeContract checks the checkout before and after each command. Persistent content changes, extra entries, or a changed index or `HEAD` produce `ERROR` and skip any remaining checks. Put caches, tools, and generated outputs outside the checkout. On POSIX, RangeContract checks Git's owner executable bit. Windows has no equivalent Git POSIX permission check. See the security policy for the limits of these observations.

Commands run in contract order in the checkout root with argument arrays, no shell dispatch, and the inherited process environment. Use an absolute executable path or a name found on an absolute PATH entry outside the checkout. Windows execution supports `.exe` programs. Standard input and command output are discarded. The report records exit codes, timeouts and execution errors. See [SECURITY.md](SECURITY.md) for the trust requirements.

## Reports and exit codes

Human reports are the default. `--json` emits a single object with `schema_version` (currently 1), `status`, `base_commit`, `target_commit`, `contract_sha256`, `scope`, `validation`, `changes`, `findings` and `checks`. Commit IDs and the SHA-256 of the exact contract bytes are present when available. Change records contain the status letter, paths in source-to-destination order when applicable, and a similarity score for renames and copies. Unavailable optional values are `null`.

| Exit | Status | Meaning |
| --- | --- | --- |
| `0` | `PASS` | Scope passed and all required checks passed, or validation was `NOT_REQUIRED`. |
| `1` | `FAIL` | Scope failed, a required check exited nonzero or timed out, or required validation was skipped and is `INCOMPLETE`. |
| `2` | `ERROR` | The contract, Git range, checkout fidelity, or validation execution could not be established. |

Scope failure skips all commands. Skipped checks appear individually with a reason. The report provides local observations and a contract fingerprint. It provides no cryptographic authentication of execution evidence.

RangeContract is available under the [MIT License](LICENSE).
