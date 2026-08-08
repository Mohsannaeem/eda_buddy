"""Tests for the decisions the CLI makes before it calls make: which component,
which simulator, and which Makefile target.

These encode deliberate choices — never guess between two simulators, prefer a
group over a same-named test — so they are worth pinning. Run standalone:

    python tests/test_resolution.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from eda_buddy.config import Project, ProjectError  # noqa: E402
from eda_buddy.runner import Runner                 # noqa: E402
from eda_buddy.toolchain import resolve_tool        # noqa: E402


class _Log:
    """Collects warnings so tests can assert on them."""

    def __init__(self):
        self.warnings = []

    def warning(self, msg):
        self.warnings.append(msg)

    def info(self, msg):
        pass

    header = success = error = info


def _project(tools=None, components=None):
    data = {"project_name": "T", "paths": {"root": "run"}}
    if tools:
        data["tools"] = tools
    p = Project("project_structure.yaml", data, _Log())
    p.components = components or {}
    return p


def _comp(tool_settings, groups=None, tests=()):
    build = {"tool_settings": {t: {} for t in tool_settings}}
    run = {
        "test_config": {"entry_points": [{"name": t} for t in tests]},
        "groups": groups or {},
    }
    return (build, run)


def _expect_error(fn, needle):
    try:
        fn()
    except ProjectError as e:
        assert needle in str(e), "expected {!r} in error, got: {}".format(needle, e)
        return
    raise AssertionError("expected ProjectError containing {!r}".format(needle))


# ── simulator resolution ──────────────────────────────────────────────────────

def test_single_toolchain_is_inferred():
    p = _project(components={"c": _comp(["xcelium"])})
    assert resolve_tool(p, "c") == "xcelium"


def test_two_toolchains_is_an_error_not_a_guess():
    p = _project(components={"c": _comp(["vcs", "questa"])})
    _expect_error(lambda: resolve_tool(p, "c"), "ambiguous")


def test_project_default_breaks_the_tie():
    p = _project(tools={"simulator": "questa"}, components={"c": _comp(["vcs", "questa"])})
    assert resolve_tool(p, "c") == "questa"


def test_explicit_tool_wins_over_project_default():
    p = _project(tools={"simulator": "questa"}, components={"c": _comp(["vcs", "questa"])})
    assert resolve_tool(p, "c", explicit="vcs") == "vcs"


def test_no_toolchain_declared_is_an_error():
    p = _project(components={"c": _comp([])})
    _expect_error(lambda: resolve_tool(p, "c"), "no tool_settings")


def test_unsupported_tool_rejected():
    p = _project(components={"c": _comp(["questa"])})
    _expect_error(lambda: resolve_tool(p, "c", explicit="modelsim"), "unsupported tool")


# ── component resolution ──────────────────────────────────────────────────────

def test_sole_component_is_the_default():
    p = _project(components={"only": _comp(["questa"])})
    assert p.resolve_component() == "only"


def test_multiple_components_require_comp():
    p = _project(components={"a": _comp(["questa"]), "b": _comp(["questa"])})
    _expect_error(p.resolve_component, "--comp is required")


def test_unknown_component_is_rejected():
    p = _project(components={"a": _comp(["questa"])})
    _expect_error(lambda: p.resolve_component("nope"), "unknown component")


# ── target resolution ─────────────────────────────────────────────────────────

def _runner(components):
    p = _project(components=components)
    return Runner(p, make_exe="make", dry_run=True), p


def test_test_name_resolves_to_run_target():
    r, _ = _runner({"c": _comp(["questa"], tests=["t1"])})
    assert r.run_target("c", "questa", "t1") == "questa_run_c_t1"


def test_group_name_resolves_to_run_target():
    r, _ = _runner({"c": _comp(["questa"], groups={"smoke": ["t1"]}, tests=["t1"])})
    assert r.run_target("c", "questa", "smoke") == "questa_run_c_smoke"


def test_group_wins_a_name_collision_and_warns():
    r, p = _runner({"c": _comp(["questa"], groups={"dup": ["t1"]}, tests=["dup"])})
    assert r.run_target("c", "questa", "dup") == "questa_run_c_dup"
    assert any("both a group and a test" in w for w in p.log.warnings), "collision must warn"


def test_unknown_target_lists_what_is_available():
    r, _ = _runner({"c": _comp(["questa"], groups={"smoke": []}, tests=["t1"])})
    _expect_error(lambda: r.run_target("c", "questa", "nope"), "smoke")


def test_build_target_shape():
    r, _ = _runner({"c": _comp(["questa"])})
    assert r.build_target("c", "questa") == "questa_build_c"


# ── default group for `all` ───────────────────────────────────────────────────

def test_regression_is_the_default_group():
    r, _ = _runner({"c": _comp(["questa"], groups={"smoke": [], "regression": []})})
    assert r.default_group("c") == "regression"


def test_sole_group_is_the_default():
    r, _ = _runner({"c": _comp(["questa"], groups={"nightly": []})})
    assert r.default_group("c") == "nightly"


def test_ambiguous_default_group_is_an_error():
    r, _ = _runner({"c": _comp(["questa"], groups={"a": [], "b": []})})
    _expect_error(lambda: r.default_group("c"), "name the target")


if __name__ == "__main__":
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("  ok   {}".format(name))
            except AssertionError as e:
                failures.append("{}: {}".format(name, e))
                print("  FAIL {}".format(name))
    if failures:
        print("\n" + "\n".join(failures))
        sys.exit(1)
    print("PASS - resolution behavior verified")
