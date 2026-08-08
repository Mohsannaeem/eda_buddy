"""EDA Buddy command-line interface.

    eda-buddy gen                          generate Makefile + filelists
    eda-buddy build  --comp X              compile + elaborate
    eda-buddy run    <test|group> --comp X simulate
    eda-buddy all    [target]    --comp X  gen -> build -> run, serial
    eda-buddy report | clean

Every command resolves a target in the generated Makefile and hands it to make.
Nothing here re-implements how a simulation is launched.
"""

import argparse
import os
import sys

from . import __version__
from .config import ProjectError, load_project
from .runner import Runner
from .toolchain import SUPPORTED_TOOLS, resolve_make, resolve_tool

_LEGACY_FLAG = "--gen-makefile"


def _add_common(p):
    # $EDA_BUDDY_PROJECT_CFG lets an environment script point at the project once,
    # so commands can be run from any directory without repeating --project-cfg.
    p.add_argument("--project-cfg",
                   default=os.environ.get("EDA_BUDDY_PROJECT_CFG") or "project_structure.yaml",
                   help="Path to project structure YAML "
                        "(default: $EDA_BUDDY_PROJECT_CFG or %(default)s)")
    p.add_argument("--make", default=None,
                   help="make executable. Overrides tools.make and $EDA_BUDDY_MAKE")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the make command instead of running it")


def _add_target_opts(p):
    p.add_argument("--comp", default=None,
                   help="Component name. Required when the project declares more than one")
    p.add_argument("--tool", default=None, choices=SUPPORTED_TOOLS,
                   help="Simulator. Overrides tools.simulator and inference from tool_settings")


def _add_run_opts(p):
    # Tri-state: unset leaves the run.yaml default in force, so a flag's absence
    # is not the same as switching it off.
    for name, dest, helptext in (
        ("waves", "waves", "wave dumping (runtime.debug.dump_waves)"),
        ("gui",   "gui",   "GUI mode (runtime.debug.gui_mode)"),
        ("quiet", "quiet", "quiet mode: log only, no terminal output (runtime.debug.quiet_sim)"),
    ):
        g = p.add_mutually_exclusive_group()
        g.add_argument(f"--{name}", dest=dest, action="store_const", const="1",
                       default=None, help=f"Enable {helptext}")
        g.add_argument(f"--no-{name}", dest=dest, action="store_const", const="0",
                       help=f"Disable {helptext}")

    p.add_argument("--wave-format", default=None, choices=("vcd", "wlf"),
                   help="Waveform format, overriding runtime.debug.wave_format")
    p.add_argument("--rms-id", default=None, metavar="ID",
                   help="Push this session's results to RMS regression ID, "
                        "overriding the group's rms_id")
    p.add_argument("--verbosity", default=None,
                   help="Override UVM verbosity for this run, e.g. UVM_HIGH (VERBOSITY=)")
    p.add_argument("-j", "--jobs", type=int, default=None, metavar="N",
                   help="Run a group's tests N-way parallel. Generation and build stay serial")


def build_parser():
    p = argparse.ArgumentParser(
        prog="eda-buddy",
        description="EDA Buddy - UVM simulation manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"eda-buddy {__version__}")
    sub = p.add_subparsers(dest="command")

    g = sub.add_parser("gen", help="Generate the Makefile and filelists")
    _add_common(g)

    b = sub.add_parser("build", help="Compile and elaborate a component")
    _add_common(b)
    _add_target_opts(b)

    r = sub.add_parser("run", help="Run a test or a regression group")
    _add_common(r)
    _add_target_opts(r)
    _add_run_opts(r)
    r.add_argument("target", help="Test name or group name")

    a = sub.add_parser("all", help="Generate, build, then run - in that order")
    _add_common(a)
    _add_target_opts(a)
    _add_run_opts(a)
    a.add_argument("target", nargs="?", default=None,
                   help="Test or group to run (default: the 'regression' group)")

    rep = sub.add_parser("report", help="Print the pass/fail summary")
    _add_common(rep)

    cl = sub.add_parser("clean", help="Remove build/work/run directories")
    _add_common(cl)

    return p


def _run_vars(args):
    """Map CLI flags onto the Makefile variables.

    A None here means "not specified", and Runner.make drops it, leaving the
    per-component default from run.yaml in force.
    """
    return {
        "WAVES":       getattr(args, "waves", None),
        "GUI":         getattr(args, "gui", None),
        "QUIET":       getattr(args, "quiet", None),
        "WAVE_FORMAT": getattr(args, "wave_format", None),
        "VERBOSITY":   getattr(args, "verbosity", None),
        "RMS_ID":      getattr(args, "rms_id", None),
        "JOBS":        getattr(args, "jobs", None),
    }


def _normalize_argv(argv):
    """Accept the pre-subcommand invocation documented in README section 2.

    `eda_buddy.py --gen-makefile [--project-cfg X]` predates subcommands and is
    baked into user scripts, so it is rewritten to `gen` rather than rejected.
    """
    if _LEGACY_FLAG in argv:
        rest = [a for a in argv if a != _LEGACY_FLAG]
        print("[EDA Buddy] note: --gen-makefile is deprecated; use 'eda-buddy gen'.",
              file=sys.stderr)
        return ["gen"] + rest
    return argv


def main(argv=None):
    argv = _normalize_argv(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    try:
        project = load_project(args.project_cfg)

        # `gen` touches no external tool, so it must not require make to exist.
        make_exe = None if args.command == "gen" else resolve_make(project, args.make)
        runner = Runner(project, make_exe=make_exe, dry_run=args.dry_run)

        if args.command == "gen":
            return runner.generate()
        if args.command == "report":
            return runner.cmd_report()
        if args.command == "clean":
            return runner.cmd_clean()

        comp = project.resolve_component(args.comp)
        tool = resolve_tool(project, comp, args.tool)

        if args.command == "build":
            return runner.cmd_build(comp, tool)
        if args.command == "run":
            return runner.cmd_run(comp, tool, args.target, _run_vars(args))
        if args.command == "all":
            return runner.cmd_all(comp, tool, args.target, _run_vars(args))

        parser.error(f"unhandled command '{args.command}'")

    except ProjectError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[EDA Buddy] interrupted.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"[ERROR] Critical tool failure: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
