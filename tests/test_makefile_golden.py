"""
Golden-file test for the Makefile generator.

Purpose: prove that refactors (package restructure, CLI additions) do not change
a single byte of what EDA Buddy generates for an unchanged project. Any intended
change to generated output must be an explicit, reviewed golden update:

    python tests/test_makefile_golden.py --update

Run the check:

    python tests/test_makefile_golden.py        # standalone, no pytest needed
    pytest tests/                               # also works if pytest is installed

The fixtures are hermetic — they do not reference the Output/ submodule or any
machine-specific path, so this runs identically on any checkout.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

_TESTS_DIR   = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT   = os.path.dirname(_TESTS_DIR)
_FIXTURE_DIR = os.path.join(_TESTS_DIR, "fixtures")
_GOLDEN_DIR  = os.path.join(_TESTS_DIR, "golden")

# The entry point is invoked as a subprocess rather than imported, so this test
# exercises whatever `python eda_buddy.py` resolves to — including the shim left
# behind by the src/ restructure. If the shim breaks, this test fails.
_ENTRY = os.path.join(_REPO_ROOT, "eda_buddy.py")

# Lines whose value is an absolute filesystem path to EDA Buddy's own scripts.
# The path legitimately changes when the package layout changes, so the whole
# right-hand side is normalized away; everything else must match exactly.
_VOLATILE_LINE = re.compile(
    r"^(REPORT_SCRIPT|PUSH_SCRIPT|REPORT_CMD|PUSH_CMD|REPORT_OK|PUSH_OK)(\s*):=.*$",
    re.MULTILINE,
)

# The integrity stamp is a hash of the real, un-normalized content, so it changes
# with the temp directory the fixture was generated in. Its correctness is covered
# by test_checksum.py; here it is only noise.
_CHECKSUM_LINE = re.compile(r"^## eda-buddy-checksum: sha256:[0-9a-f]+$", re.MULTILINE)


def _materialize(tmp):
    """Copy fixtures into tmp, resolving __FIXDIR__ to the temp fixture dir.

    Returns (project_cfg_path, fixdir, root). Paths are written POSIX-style
    because that is what the generator emits and what Make consumes.
    """
    fixdir = os.path.join(tmp, "fixtures").replace("\\", "/")
    root   = os.path.join(tmp, "run").replace("\\", "/")
    os.makedirs(fixdir)

    for fname in sorted(os.listdir(_FIXTURE_DIR)):
        src = os.path.join(_FIXTURE_DIR, fname)
        if not os.path.isfile(src):
            continue
        with open(src, encoding="utf-8") as f:
            text = f.read()
        with open(os.path.join(fixdir, fname), "w", encoding="utf-8") as f:
            f.write(text.replace("__FIXDIR__", fixdir))

    project_cfg = os.path.join(tmp, "project_structure.yaml").replace("\\", "/")
    with open(project_cfg, "w", encoding="utf-8") as f:
        f.write(
            "project_name: GOLDEN_FIXTURE_PROJECT\n"
            "paths:\n"
            f"  root: {root}\n"
            "  filelists: filelists\n"
            "  makefile: .\n"
            "hooks:\n"
            "  pre: echo global-pre\n"
            "  post: echo global-post\n"
            "components:\n"
            "- name: comp_a\n"
            f"  build_cfg: {fixdir}/comp_a_build.yaml\n"
            f"  runtime_cfg: {fixdir}/comp_a_run.yaml\n"
            "- name: comp_b\n"
            f"  build_cfg: {fixdir}/comp_b_build.yaml\n"
            f"  runtime_cfg: {fixdir}/comp_b_run.yaml\n"
        )
    return project_cfg, fixdir, root


def _normalize(text, fixdir, root, tmp):
    """Replace machine-specific absolute paths with stable placeholders.

    Longest paths first: root and fixdir both live under tmp, so substituting
    tmp first would leave partial matches behind.
    """
    for needle, token in ((root, "__ROOT__"), (fixdir, "__FIXDIR__"), (tmp, "__TMP__")):
        for variant in (needle, needle.replace("/", "\\")):
            text = text.replace(variant, token)
    text = _CHECKSUM_LINE.sub("## eda-buddy-checksum: __CHECKSUM__", text)
    return _VOLATILE_LINE.sub(r"\1\2:= __EDA_BUDDY_SCRIPT__", text)


def _generate():
    """Run the generator in a temp tree; return {artifact_name: normalized_text}."""
    tmp = tempfile.mkdtemp(prefix="eda_buddy_golden_")
    try:
        project_cfg, fixdir, root = _materialize(tmp)

        proc = subprocess.run(
            [sys.executable, _ENTRY, "--gen-makefile", "--project-cfg", project_cfg],
            capture_output=True, text=True, cwd=tmp,
        )
        if proc.returncode != 0:
            raise AssertionError(
                "generator exited {}\n--- stdout ---\n{}\n--- stderr ---\n{}".format(
                    proc.returncode, proc.stdout, proc.stderr
                )
            )

        artifacts = {}
        # Read with the platform default encoding, matching the generator's own
        # open(path, "w") so the round-trip is symmetric on Windows (cp1252).
        for rel in ("Makefile", "filelists/comp_a.f", "filelists/comp_b.f"):
            path = os.path.join(root, rel)
            if not os.path.exists(path):
                raise AssertionError("generator did not produce {}".format(rel))
            with open(path) as f:
                artifacts[rel.replace("/", "__")] = _normalize(f.read(), fixdir, root, tmp)
        return artifacts
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _golden_path(name):
    return os.path.join(_GOLDEN_DIR, name + ".golden")


def update_golden():
    os.makedirs(_GOLDEN_DIR, exist_ok=True)
    for name, text in _generate().items():
        with open(_golden_path(name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print("[golden] wrote {}".format(_golden_path(name)))


def test_generated_output_matches_golden():
    artifacts = _generate()
    failures = []

    for name, actual in sorted(artifacts.items()):
        path = _golden_path(name)
        if not os.path.exists(path):
            failures.append("missing golden {} — run with --update".format(path))
            continue
        with open(path, encoding="utf-8", newline="") as f:
            expected = f.read()
        # Normalize line endings: git may check the golden out with CRLF.
        if actual.replace("\r\n", "\n") != expected.replace("\r\n", "\n"):
            failures.append(_diff(name, expected, actual))

    assert not failures, "\n\n".join(failures)


def _diff(name, expected, actual):
    import difflib
    lines = difflib.unified_diff(
        expected.replace("\r\n", "\n").splitlines(),
        actual.replace("\r\n", "\n").splitlines(),
        fromfile="golden/" + name, tofile="generated/" + name, lineterm="", n=2,
    )
    return "{} differs from golden:\n{}".format(name, "\n".join(lines))


if __name__ == "__main__":
    if "--update" in sys.argv:
        update_golden()
        sys.exit(0)
    try:
        test_generated_output_matches_golden()
    except AssertionError as e:
        print("FAIL\n{}".format(e))
        sys.exit(1)
    print("PASS — generated Makefile and filelists match golden")
