"""Resolution of the two external things EDA Buddy has to locate: which
simulator to drive, and which `make` binary to drive it with.
"""

import os
import shutil
import subprocess

from .config import ProjectError

SUPPORTED_TOOLS = ("vcs", "questa", "xcelium")


def resolve_tool(project, comp_name, explicit=None):
    """Pick the simulator for a component.

    Order: --tool > tools.simulator > the component's only declared toolchain.
    Never guesses between several: a wrong pick here silently runs the wrong
    build, which is worse than an error.
    """
    if explicit:
        if explicit not in SUPPORTED_TOOLS:
            raise ProjectError("unsupported tool '{}'. Supported: {}".format(
                explicit, ", ".join(SUPPORTED_TOOLS)))
        return explicit

    if project.default_tool:
        if project.default_tool not in SUPPORTED_TOOLS:
            raise ProjectError("tools.simulator is '{}'; expected one of {}".format(
                project.default_tool, ", ".join(SUPPORTED_TOOLS)))
        return project.default_tool

    build_cfg, _ = project.component(comp_name)
    declared = [t for t in SUPPORTED_TOOLS if t in (build_cfg.get('tool_settings', {}) or {})]

    if len(declared) == 1:
        return declared[0]
    if not declared:
        raise ProjectError(
            "component '{}' declares no tool_settings, so there is nothing to build "
            "with. Add a tool_settings block, or pass --tool.".format(comp_name))
    raise ProjectError(
        "component '{}' declares {} toolchains ({}), so the simulator is ambiguous. "
        "Pass --tool, or set tools.simulator in the project file.".format(
            comp_name, len(declared), ", ".join(declared)))


def resolve_make(project, explicit=None):
    """Locate the make executable.

    Order: --make > tools.make > $EDA_BUDDY_MAKE > PATH. Reported as one error
    naming all four, because "make: not found" on a machine where make is
    installed but off PATH is an unhelpful thing to be told.
    """
    for candidate in (explicit, project.make_exe, os.environ.get("EDA_BUDDY_MAKE")):
        if candidate:
            found = shutil.which(candidate) or (candidate if os.path.isfile(candidate) else None)
            if found:
                return os.path.normpath(found)
            raise ProjectError("configured make '{}' does not exist".format(candidate))

    found = shutil.which("make")
    if found:
        return os.path.normpath(found)

    raise ProjectError(
        "could not find 'make'. Set one of, in order of precedence:\n"
        "  --make <path>\n"
        "  tools.make in project_structure.yaml   (e.g. C:/cygwin64/bin/make.exe)\n"
        "  $EDA_BUDDY_MAKE\n"
        "  or put make on PATH")


def to_make_path(make_exe, path):
    """Convert a Windows path for a Cygwin make.

    Cygwin's make reports paths as /cygdrive/... and is happiest being given
    them. If a cygpath sits beside the resolved make, use it; otherwise the
    path is passed through untouched, which is correct for native make.
    """
    cygpath = os.path.join(os.path.dirname(make_exe), "cygpath.exe")
    if not os.path.isfile(cygpath):
        return path
    try:
        out = subprocess.run([cygpath, "-u", path], capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return path
