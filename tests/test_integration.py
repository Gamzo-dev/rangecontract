import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

from rangecontract import __version__


class Repo:
    def __init__(self, root):
        self.root = root
        self.env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git_bytes(self, *argv, data=None):
        return subprocess.run(
            ["git", "-C", str(self.root), *argv], env=self.env, input=data,
            check=True, capture_output=True, timeout=30,
        ).stdout

    def git(self, *argv):
        return self.git_bytes(*argv).decode("utf-8").strip()

    def commit_raw_path(self, raw_path):
        oid = self.git_bytes("hash-object", "-w", "--stdin", data=b"contents").strip()
        self.git_bytes(
            "-c", "core.protectNTFS=false", "update-index", "-z", "--index-info",
            data=b"100644 " + oid + b"\t" + raw_path + b"\0",
        )
        self.git("commit", "--quiet", "-m", "add raw path")

    def commit_tree(self, entries):
        """Write a tree even when the filesystem cannot materialize its paths."""
        base = self.git("rev-parse", "HEAD")
        records = []
        for mode, path, contents in entries:
            oid = self.git_bytes("hash-object", "-w", "--stdin", data=contents).strip()
            records.append(mode + b" blob " + oid + b"\t" + path + b"\0")
        tree = self.git_bytes("mktree", "-z", data=b"".join(records)).decode().strip()
        target = self.git("commit-tree", tree, "-p", base, "-m", "tree fixture")
        self.git("update-ref", "refs/heads/main", target)
        self.git("read-tree", target)
        return target

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))

    def commit(self, message):
        self.git("add", "--all")
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "checkout with spaces"
    root.mkdir()
    repository = Repo(root)
    repository.git("init", "--quiet", "--initial-branch=main", "--template=")
    repository.git("config", "user.name", "RangeContract Tests")
    repository.git("config", "user.email", "rangecontract@example.invalid")
    repository.git("config", "core.autocrlf", "false")
    return repository


def cli(*args, **kwargs):
    return subprocess.run(
        [sys.executable, "-I", "-B", "-m", "rangecontract", *map(str, args)],
        capture_output=True, text=True, timeout=45, **kwargs,
    )


def contract(repo, base, *, allowed=("**",), forbidden=(), maximum=20, checks=()):
    path = repo.root.parent / "policy.toml"
    lines = [
        f'base_commit = "{base}"',
        f"allowed_paths = {json.dumps(allowed, ensure_ascii=False)}",
        f"forbidden_paths = {json.dumps(forbidden, ensure_ascii=False)}",
        f"max_changed_files = {maximum}",
    ]
    for name, argv, timeout in checks:
        lines.extend([
            "[[checks]]",
            f"name = {json.dumps(name)}",
            f"argv = {json.dumps(argv)}",
            f"timeout_seconds = {timeout}",
        ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def verify(repo, path, *flags):
    completed = cli("verify", "--repo", repo.root, "--contract", path, "--json", *flags)
    assert completed.stderr == ""
    return completed.returncode, json.loads(completed.stdout)


def python_check(source, name="validate", timeout=15):
    return name, [sys.executable, "-I", "-B", "-c", source], timeout


def test_init_version_and_no_overwrite(repo):
    base = repo.commit("base")
    path = repo.root.parent / "new.toml"
    args = ("init", "--repo", repo.root, "--base", "main", "--contract", path)
    assert cli(*args).returncode == 0
    assert base.encode() in path.read_bytes()
    old = path.read_bytes()
    assert cli(*args).returncode == 2
    assert path.read_bytes() == old
    assert verify(repo, path)[0] == 0
    version = cli("version")
    assert (version.returncode, version.stdout.strip()) == (0, f"rangecontract {__version__}")


def test_exact_range_modification_addition_deletion_and_hash(repo):
    repo.write("src/edit.txt", "old")
    repo.write("src/remove.txt", "old content for the deletion")
    base = repo.commit("base")
    repo.write("src/edit.txt", "updated")
    repo.write("src/new [1].txt", "entirely different added contents")
    (repo.root / "src/remove.txt").unlink()
    target = repo.commit("target")
    repo.git("tag", "-a", "reviewed", "-m", "reviewed")
    policy = contract(repo, base, allowed=("src/*.txt",), maximum=3)

    code, report = verify(repo, policy, "--target", "reviewed")
    assert (code, report["status"], report["validation"]) == (0, "PASS", "NOT_REQUIRED")
    assert (report["base_commit"], report["target_commit"]) == (base, target)
    assert report["contract_sha256"] == hashlib.sha256(policy.read_bytes()).hexdigest()
    assert [(item["status"], item["paths"][0]) for item in report["changes"]] == [
        ("M", "src/edit.txt"), ("A", "src/new [1].txt"), ("D", "src/remove.txt"),
    ]

    repo.write("in-progress.txt", "untracked")
    repo.write("src/edit.txt", "staged edit unrelated to the target")
    repo.git("add", "src/edit.txt")
    assert verify(repo, policy, "--target", target) == (code, report)
    human = cli("verify", "--repo", repo.root, "--contract", policy, "--target", target)
    assert human.returncode == 0
    assert all(token in human.stdout for token in ("PASS", base, target, report["contract_sha256"]))


def test_forbidden_precedence_case_sensitivity_and_limit(repo):
    base = repo.commit("base")
    repo.write("src/keys/hidden.txt", "private")
    repo.write("SRC.txt", "case-sensitive scope")
    repo.write("src/normal.txt", "ordinary")
    repo.commit("target")
    policy = contract(repo, base, allowed=("src/**", "src.txt"), forbidden=("**/keys/**",), maximum=2)
    code, report = verify(repo, policy)
    assert (code, report["status"], report["scope"]) == (1, "FAIL", "FAIL")
    assert [(item["code"], item["path"]) for item in report["findings"]] == [
        ("changed_file_limit", None),
        ("path_not_allowed", "SRC.txt"),
        ("forbidden_path", "src/keys/hidden.txt"),
    ]


@pytest.mark.parametrize("operation", ["rename", "copy"])
@pytest.mark.parametrize("blocked", ["protected/original.txt", "src/destination.txt"])
def test_rename_and_copy_check_both_paths_and_count_once(repo, operation, blocked):
    repo.write("protected/original.txt", "unchanged content")
    base = repo.commit("base")
    target = repo.root / "src/destination.txt"
    target.parent.mkdir()
    source = repo.root / "protected/original.txt"
    if operation == "rename":
        source.rename(target)
    else:
        shutil.copyfile(source, target)
    repo.commit("target")
    repo.git("config", "diff.renames", "false")
    repo.git("config", "diff.renameLimit", "1")
    policy = contract(repo, base, forbidden=(blocked,), maximum=1)
    code, report = verify(repo, policy)
    assert code == 1
    assert report["changes"] == [{
        "status": "R" if operation == "rename" else "C",
        "paths": ["protected/original.txt", "src/destination.txt"],
        "similarity": 100,
    }]
    assert [(item["code"], item["path"]) for item in report["findings"]] == [
        ("forbidden_path", blocked),
    ]


def test_literal_unicode_and_option_like_paths(repo):
    base = repo.commit("base")
    paths = ("-n.txt", "[literal].txt", "snowman-\u2603.txt")
    for path in paths:
        repo.write(path, path)
    repo.commit("target")
    policy = contract(repo, base, allowed=("-n.txt", "[literal].txt", "snowman-?.txt"))
    code, report = verify(repo, policy)
    assert code == 0
    assert [item["paths"][0] for item in report["changes"]] == list(paths)


def test_git_paths_with_tabs_and_newlines_are_preserved(repo):
    base = repo.commit("base")
    path = "src/line\nbreak\tname.txt"
    repo.commit_raw_path(path.encode("utf-8"))
    policy = contract(repo, base, allowed=("src/*.txt",), forbidden=(path,))
    code, report = verify(repo, policy)
    assert code == 1
    assert report["changes"][0]["paths"] == [path]
    human = cli("verify", "--repo", repo.root, "--contract", policy)
    assert r"line\nbreak\tname.txt" in human.stdout
    assert path not in human.stdout


def test_non_utf8_git_path_fails_closed(repo):
    base = repo.commit("base")
    repo.commit_raw_path(b"non-utf8-\xff.txt")
    code, report = verify(repo, contract(repo, base))
    assert (code, report["status"]) == (2, "ERROR")
    assert "valid UTF-8" in report["findings"][0]["message"]


def test_inexact_copy_uses_committed_attributes_and_blobs(repo):
    repo.write("protected.txt", "a line with consistent contents\r\n" * 20)
    base = repo.commit("base")
    repo.write("copy.txt", "a line with consistent contents\n" * 20 + "another line\n")
    repo.commit("target")
    policy = contract(repo, base, forbidden=("protected.txt",))
    code, report = verify(repo, policy)
    assert (code, report["changes"][0]["status"]) == (1, "C")
    assert report["changes"][0]["paths"] == ["protected.txt", "copy.txt"]

    repo.write(".gitattributes", "*.txt -diff\n")
    repo.git("add", ".gitattributes")
    repo.write("copy.txt", "dirty worktree contents")
    assert verify(repo, policy) == (code, report)


def test_nonancestor_and_invalid_target_are_errors(repo):
    base = repo.commit("base")
    left = repo.commit("left")
    repo.git("checkout", "--quiet", "--detach", base)
    right = repo.commit("right")
    policy = contract(repo, left)
    code, report = verify(repo, policy)
    assert (code, report["base_commit"], report["target_commit"]) == (2, left, right)
    assert "not an ancestor" in report["findings"][0]["message"]
    assert verify(repo, policy, "--target=--help")[0] == 2


def test_annotated_tag_id_is_not_a_pinned_base_commit(repo):
    repo.commit("base")
    repo.git("tag", "-a", "tag", "-m", "tag")
    policy = contract(repo, repo.git("rev-parse", "tag"))
    code, report = verify(repo, policy)
    assert code == 2
    assert "identify the commit itself" in report["findings"][0]["message"]


def test_replacement_objects_do_not_change_scope(repo, monkeypatch):
    repo.write("protected.txt", "base state")
    base = repo.commit("base")
    repo.write("protected.txt", "target state")
    target = repo.commit("target")
    repo.git("replace", target, base)
    monkeypatch.setenv("GIT_DIR", str(repo.root.parent / "elsewhere"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "diff.renames")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")
    policy = contract(repo, base, forbidden=("protected.txt",))
    assert verify(repo, policy)[0] == 1


def test_unsupported_type_change_is_an_error(repo):
    repo.write("entry", "link target")
    base = repo.commit("base")
    blob = repo.git("rev-parse", "HEAD:entry")
    repo.git("update-index", "--cacheinfo", f"120000,{blob},entry")
    repo.git("commit", "--quiet", "-m", "convert file to a symlink")
    policy = contract(repo, base)
    code, report = verify(repo, policy)
    assert (code, report["status"], report["scope"]) == (2, "ERROR", "NOT_EVALUATED")
    assert "unsupported Git change status" in report["findings"][0]["message"]


def test_checks_need_opt_in_use_literal_arguments_and_target_cwd(repo):
    repo.write("tracked.txt", "expected")
    base = repo.commit("base")
    marker = repo.root.parent / "ran.txt"
    value = "& > shell-created.txt"
    name, argv, timeout = python_check(
        "import sys; from pathlib import Path; "
        "assert Path('tracked.txt').read_text() == 'expected'; "
        "assert sys.argv[2] == '& > shell-created.txt'; "
        "Path(sys.argv[1]).write_text('ran')"
    )
    policy = contract(repo, base, checks=((name, [*argv, str(marker), value], timeout),))
    code, report = verify(repo, policy)
    assert (code, report["scope"], report["validation"]) == (1, "PASS", "INCOMPLETE")
    assert report["checks"][0]["status"] == "SKIPPED"
    assert not marker.exists()

    code, report = verify(repo, policy, "--run-checks")
    assert (code, report["validation"], report["checks"][0]["exit_code"]) == (0, "PASS", 0)
    assert marker.read_text() == "ran"
    assert not (repo.root / "shell-created.txt").exists()


def test_scope_failure_does_not_run_checks(repo):
    base = repo.commit("base")
    repo.write("blocked.txt", "blocked")
    repo.commit("target")
    policy = contract(repo, base, allowed=(), checks=(python_check("raise SystemExit(12)"),))
    code, report = verify(repo, policy, "--run-checks")
    assert (code, report["scope"], report["checks"][0]["status"]) == (1, "FAIL", "SKIPPED")


def test_contract_from_inside_checkout_cannot_run(repo):
    base = repo.commit("base")
    path = contract(repo, base, checks=(python_check("raise SystemExit(12)"),))
    inside = repo.root / "policy.toml"
    path.rename(inside)
    repo.commit("add untrusted contract")
    code, report = verify(repo, inside, "--run-checks")
    assert (code, report["validation"], report["checks"][0]["status"]) == (2, "INCOMPLETE", "SKIPPED")


@pytest.mark.parametrize("state", ["staged", "unstaged", "assume-unchanged", "ignored", "untracked", "wrong-head"])
def test_checks_refuse_dirty_or_mismatched_checkout(repo, state):
    repo.write(".gitignore", "ignored.txt\n")
    repo.write("tracked.txt", "base")
    base = repo.commit("base")
    target = repo.commit("target")
    if state == "wrong-head":
        repo.git("checkout", "--quiet", "--detach", base)
    elif state in {"ignored", "untracked"}:
        repo.write(f"{state}.txt", "unexpected")
    else:
        if state == "assume-unchanged":
            repo.git("update-index", "--assume-unchanged", "tracked.txt")
        repo.write("tracked.txt", "modified")
        if state == "staged":
            repo.git("add", "tracked.txt")
            repo.write("tracked.txt", "base")
    policy = contract(repo, base, checks=(python_check("raise SystemExit(12)"),))
    code, report = verify(repo, policy, "--target", target, "--run-checks")
    assert (code, report["scope"], report["validation"]) == (2, "PASS", "ERROR")
    assert report["checks"][0]["status"] == "ERROR"
    assert report["checks"][0]["exit_code"] is None


def test_status_does_not_execute_configured_filters(repo):
    repo.write(".gitattributes", "*.txt filter=external\n")
    repo.write("tracked.txt", "base")
    base = repo.commit("base")
    repo.git("config", "filter.external.clean", "external-filter")
    policy = contract(repo, base, checks=(python_check("pass"),))
    assert verify(repo, policy)[0] == 1
    code, report = verify(repo, policy, "--run-checks")
    assert code == 2
    assert "filters" in report["checks"][0]["detail"]


def test_check_that_dirties_checkout_prevents_later_checks(repo):
    base = repo.commit("base")
    policy = contract(repo, base, checks=(
        python_check("from pathlib import Path; Path('new.txt').write_text('new')", "write"),
        python_check("raise SystemExit(12)", "second"),
        python_check("raise SystemExit(12)", "third"),
    ))
    code, report = verify(repo, policy, "--run-checks")
    assert code == 2
    assert [check["status"] for check in report["checks"]] == ["ERROR", "SKIPPED", "SKIPPED"]
    assert report["checks"][0]["exit_code"] == 0
    assert "post-validation checkout" in report["checks"][0]["detail"]


def test_check_failure_and_timeout(repo):
    base = repo.commit("base")
    policy = contract(repo, base, checks=(
        python_check("raise SystemExit(7)", "nonzero"),
        python_check("import time; time.sleep(20)", "slow", timeout=1),
    ))
    code, report = verify(repo, policy, "--run-checks")
    assert (code, report["validation"]) == (1, "FAIL")
    assert [(check["status"], check["exit_code"]) for check in report["checks"]] == [
        ("FAIL", 7), ("TIMEOUT", None),
    ]


def test_missing_executable_is_an_error(repo):
    base = repo.commit("base")
    policy = contract(repo, base, checks=(("missing", ["rangecontract-missing-test-executable"], 1),))
    code, report = verify(repo, policy, "--run-checks")
    assert (code, report["checks"][0]["status"]) == (2, "ERROR")


def test_invalid_contract_still_reports_hash(repo):
    base = repo.commit("base")
    policy = contract(repo, base, maximum="true")
    code, report = verify(repo, policy)
    assert (code, report["scope"]) == (2, "NOT_EVALUATED")
    assert report["contract_sha256"] == hashlib.sha256(policy.read_bytes()).hexdigest()


@pytest.mark.parametrize("shadow", ["module", "package", "pythonpath"])
def test_installed_isolated_cli_ignores_shadowing(repo, shadow):
    base = repo.commit("base")
    repo.write("blocked.txt", "policy rejects this path")
    repo.commit("scope fixture")
    policy = contract(repo, base, forbidden=("blocked.txt",))
    source = "print('shadow fixture selected')\n"
    env = os.environ.copy()
    if shadow == "module":
        repo.write("rangecontract.py", source)
    elif shadow == "package":
        repo.write("rangecontract/__init__.py", source)
        repo.write("rangecontract/__main__.py", "")
    else:
        injected = repo.root.parent / "injected"
        package = injected / "rangecontract"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(source, encoding="utf-8")
        (package / "__main__.py").write_text("", encoding="utf-8")
        env["PYTHONPATH"] = str(injected)

    result = cli("verify", "--repo", repo.root, "--contract", policy, "--json", cwd=repo.root, env=env)
    assert result.returncode == 1
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["status"] == "FAIL"
    assert report["findings"][0]["code"] == "forbidden_path"


def test_validation_rejects_case_collisions_in_target_tree(repo):
    base = repo.commit("base")
    repo.commit_tree([
        (b"100644", b"ONE.txt", b"same bytes"),
        (b"100644", b"one.txt", b"same bytes"),
    ])
    repo.write("ONE.txt", "same bytes")
    policy = contract(repo, base, checks=(python_check("pass"),))
    code, report = verify(repo, policy, "--run-checks")
    assert (code, report["scope"], report["checks"][0]["exit_code"]) == (2, "PASS", None)
    assert "path collision" in report["checks"][0]["detail"]


def test_validation_rejects_case_mismatch_even_for_identical_bytes(repo):
    repo.write("ONE.txt", "same bytes")
    base = repo.commit("base")
    (repo.root / "ONE.txt").unlink()
    repo.write("one.txt", "same bytes")
    policy = contract(repo, base, checks=(python_check("pass"),))
    code, report = verify(repo, policy, "--run-checks")
    assert code == 2
    assert "unexpected validation checkout entry 'one.txt'" in report["checks"][0]["detail"]


def test_validation_rejects_symlink_materialized_as_text(repo):
    repo.git("config", "core.symlinks", "false")
    base = repo.commit("base")
    repo.commit_tree([(b"120000", b"link.txt", b"destination.txt")])
    repo.write("link.txt", "destination.txt")
    policy = contract(repo, base, checks=(python_check("pass"),))
    code, report = verify(repo, policy, "--run-checks")
    assert (code, report["scope"], report["checks"][0]["exit_code"]) == (2, "PASS", None)
    assert "unsupported Git tree entry b'120000'" in report["checks"][0]["detail"]


def test_validation_rejects_submodules_without_inspecting_their_checkouts(repo):
    base = repo.commit("base")
    repo.git("update-index", "--add", "--cacheinfo", f"160000,{base},vendor")
    repo.git("commit", "--quiet", "-m", "add gitlink")
    assert verify(repo, contract(repo, base))[0] == 0

    policy = contract(repo, base, checks=(python_check("pass"),))
    code, report = verify(repo, policy, "--run-checks")
    assert code == 2
    assert "unsupported Git tree entry b'160000'" in report["checks"][0]["detail"]


@pytest.mark.parametrize(("attribute", "blob", "worktree"), [
    ("ident", b"$Id$\n", b"$Id: alternate $\n"),
    ("text eol=crlf", b"one\ntwo\n", b"one\r\ntwo\r\n"),
])
def test_validation_rejects_transformed_bytes_git_status_accepts(repo, attribute, blob, worktree):
    base = repo.commit("base")
    attrs = f"tracked.txt {attribute}\n".encode()
    repo.commit_tree([
        (b"100644", b".gitattributes", attrs),
        (b"100644", b"tracked.txt", blob),
    ])
    (repo.root / ".gitattributes").write_bytes(attrs)
    (repo.root / "tracked.txt").write_bytes(worktree)
    assert repo.git("status", "--porcelain") == ""
    policy = contract(repo, base, checks=(python_check("pass"),))
    code, report = verify(repo, policy, "--run-checks")
    assert (code, report["scope"], report["checks"][0]["exit_code"]) == (2, "PASS", None)
    assert "raw content differs" in report["checks"][0]["detail"]


def test_validation_rejects_hidden_nested_git_directory(repo):
    repo.write("src/tracked.txt", "ordinary content")
    base = repo.commit("base")
    repo.write("src/.git/untracked.txt", "unexpected content")
    assert repo.git("status", "--porcelain", "--untracked-files=all", "--ignored=matching") == ""
    assert verify(repo, contract(repo, base))[0] == 0

    policy = contract(repo, base, checks=(python_check("pass"),))
    code, report = verify(repo, policy, "--run-checks")
    assert (code, report["scope"], report["checks"][0]["exit_code"]) == (2, "PASS", None)
    assert "unexpected validation checkout entry 'src/.git'" in report["checks"][0]["detail"]


def test_validation_does_not_follow_directory_links(repo):
    repo.write("src/tracked.txt", "ordinary content")
    base = repo.commit("base")
    outside = repo.root.parent / "outside"
    (repo.root / "src").rename(outside)
    link = repo.root / "src"
    if os.name == "nt":
        env = {**os.environ, "RANGECONTRACT_TEST_LINK": str(link), "RANGECONTRACT_TEST_DESTINATION": str(outside)}
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "New-Item -ItemType Junction -Path $env:RANGECONTRACT_TEST_LINK "
             "-Target $env:RANGECONTRACT_TEST_DESTINATION -ErrorAction Stop | Out-Null"],
            env=env, check=True, capture_output=True, timeout=15,
        )
    else:
        link.symlink_to(outside, target_is_directory=True)

    policy = contract(repo, base, checks=(python_check("pass"),))
    code, report = verify(repo, policy, "--run-checks")
    assert (code, report["checks"][0]["exit_code"]) == (2, None)
    assert "link or reparse point" in report["checks"][0]["detail"]


@pytest.mark.parametrize(("source", "detail"), [
    ("Path('tracked.txt').write_bytes(b'fake')", "raw content differs"),
    ("Path('extra-empty').mkdir()", "unexpected validation checkout entry"),
    ("Path('src/.git').mkdir()", "unexpected validation checkout entry"),
])
def test_fidelity_is_checked_after_the_final_command(repo, source, detail):
    repo.write("tracked.txt", "base")
    repo.write("src/tracked.txt", "ordinary content")
    base = repo.commit("base")
    policy = contract(repo, base, checks=(python_check(f"from pathlib import Path; {source}"),))

    code, report = verify(repo, policy, "--run-checks")
    assert (code, report["validation"], report["checks"][0]["status"]) == (2, "ERROR", "ERROR")
    assert report["checks"][0]["exit_code"] == 0
    assert report["checks"][0]["detail"].startswith("post-validation checkout:")
    assert detail in report["checks"][0]["detail"]


def test_scope_only_works_without_a_worktree(tmp_path):
    root = tmp_path / "bare"
    root.mkdir()
    repo = Repo(root)
    repo.git("init", "--quiet", "--bare", "--initial-branch=main", "--template=")
    repo.git("config", "user.name", "RangeContract Tests")
    repo.git("config", "user.email", "rangecontract@example.invalid")
    tree = repo.git_bytes("mktree", data=b"").decode().strip()
    base = repo.git("commit-tree", tree, "-m", "base")
    repo.git("update-ref", "refs/heads/main", base)
    assert verify(repo, contract(repo, base))[0] == 0

    policy = contract(repo, base, checks=(python_check("pass"),))
    code, report = verify(repo, policy, "--run-checks")
    assert code == 2
    assert "requires a working tree" in report["checks"][0]["detail"]


def test_validation_rejects_unicode_collision_before_running_a_command(repo):
    repo.commit("base")
    target = repo.commit_tree([
        (b"100644", "\u03b1\u0345\u0301.txt".encode(), b"first ordinary file\n"),
        (b"100644", "\u03b1\u0301\u0345.txt".encode(), b"second ordinary file\n"),
    ])
    scope_only = contract(repo, target, allowed=(), maximum=0)
    assert verify(repo, scope_only)[0] == 0

    marker = repo.root.parent / "validation-ran"
    name, argv, timeout = python_check("import sys; from pathlib import Path; Path(sys.argv[1]).touch()")
    policy = contract(repo, target, allowed=(), maximum=0, checks=((name, [*argv, str(marker)], timeout),))
    code, report = verify(repo, policy, "--run-checks")

    assert (code, report["scope"], report["validation"]) == (2, "PASS", "ERROR")
    check, = report["checks"]
    assert (check["status"], check["exit_code"]) == ("ERROR", None)
    assert "validation tree path collision" in check["detail"]
    assert not marker.exists()


def test_validation_accepts_ordinary_checkout_without_collisions(repo):
    repo.write("src/ordinary.txt", "a regular file\n")
    target = repo.commit("ordinary checkout")
    policy = contract(repo, target, allowed=(), maximum=0, checks=(python_check("pass"),))
    code, report = verify(repo, policy, "--run-checks")
    assert (code, report["scope"], report["validation"]) == (0, "PASS", "PASS")
    assert report["checks"][0]["exit_code"] == 0
