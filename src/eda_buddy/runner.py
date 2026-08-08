"""Drives the generated Makefile.

Everything that decides *how* a simulation runs — hook ordering, session
directories, per-test status lines, reporting, RMS push — lives in the Makefile
that makefile_gen writes. This module only decides *which* target to ask for and
hands it to make, so there is exactly one execution path to keep correct.
"""

import os
import shutil
import subprocess
import sys

from .config import ProjectError
from .makefile_gen import MakefileGenerator, is_pristine
from .toolchain import to_make_path


class Runner:
    def __init__(self, project, make_exe=None, dry_run=False):
        self.project  = project
        self.make_exe = make_exe
        self.dry_run  = dry_run
        self.log      = project.log

    # ── Makefile lifecycle ────────────────────────────────────────────────────

    def generate(self):
        """Generate the Makefile and filelists. Overwrites unconditionally."""
        gen = MakefileGenerator(
            self.project.components,
            self.log,
            root=self.project.root,
            flist_subdir=self.project.flist_subdir,
            makefile_subdir=self.project.makefile_sub,
            global_hooks=self.project.global_hooks,
            project_name=self.project.name,
        )
        gen.generate()
        return 0

    def is_stale(self):
        """True when any project YAML is newer than the generated Makefile."""
        path = self.project.makefile_path
        if not os.path.exists(path):
            return True
        mk_mtime = os.path.getmtime(path)
        return any(os.path.getmtime(f) > mk_mtime for f in self.project.config_files())

    def ensure_makefile(self):
        """Regenerate if missing or stale, preserving unrecognized content.

        A Makefile that does not match its own checksum was either hand-edited
        (README section 12 documents editing ROOT and *_SRC_DIR in place) or
        produced before checksums existed. Either way its content cannot be
        reproduced, so it is copied aside before being overwritten.
        """
        path = self.project.makefile_path
        exists = os.path.exists(path)

        if exists and not self.is_stale():
            return 0

        if exists and not is_pristine(path):
            backup = path + ".bak"
            if self.dry_run:
                self.log.warning(f"[dry-run] would back up modified Makefile to {backup}")
            else:
                shutil.copy2(path, backup)
                self.log.warning("=" * 70)
                self.log.warning("Makefile was modified since EDA Buddy generated it.")
                self.log.warning(f"Your version has been saved to: {backup}")
                self.log.warning("Re-apply any manual edits, or move them into the YAMLs.")
                self.log.warning("=" * 70)

        if self.dry_run:
            self.log.info(f"[dry-run] would regenerate {path}")
            return 0

        self.log.info("Project YAMLs are newer than the Makefile — regenerating.")
        return self.generate()

    # ── target resolution ─────────────────────────────────────────────────────

    def build_target(self, comp, tool):
        return f"{tool}_build_{comp}"

    def run_target(self, comp, tool, name):
        """Resolve a test or group name to a run target.

        Groups win a name collision: a group is the coarser request, and the
        generator emits both under the same target name anyway, so the group's
        recipe is what make would run regardless.
        """
        _, run_cfg = self.project.component(comp)
        groups = list((run_cfg.get('groups', {}) or {}))
        tests  = [t['name'] for t in run_cfg.get('test_config', {}).get('entry_points', [])]

        if name in groups and name in tests:
            self.log.warning(
                f"'{name}' is both a group and a test in {comp}; running the group.")
        if name in groups or name in tests:
            return f"{tool}_run_{comp}_{name}"

        raise ProjectError(
            "'{}' is not a test or group in component '{}'.\n  groups: {}\n  tests : {}".format(
                name, comp, ", ".join(groups) or "(none)", ", ".join(tests) or "(none)"))

    def default_group(self, comp):
        """The group `all` runs when none is named."""
        _, run_cfg = self.project.component(comp)
        groups = list((run_cfg.get('groups', {}) or {}))
        if "regression" in groups:
            return "regression"
        if len(groups) == 1:
            return groups[0]
        raise ProjectError(
            "component '{}' has no group named 'regression', so name the target "
            "explicitly. Available groups: {}".format(comp, ", ".join(groups) or "(none)"))

    # ── make invocation ───────────────────────────────────────────────────────

    def make(self, targets, variables=None):
        """Invoke make on the generated Makefile. Returns its exit code."""
        if not self.make_exe:
            raise ProjectError("no make executable resolved")

        cmd = [self.make_exe, "-C", to_make_path(self.make_exe, self.project.makefile_dir)]
        for key, value in sorted((variables or {}).items()):
            if value is not None and value != "":
                cmd.append(f"{key}={value}")
        cmd += list(targets)

        printable = " ".join(cmd)
        if self.dry_run:
            print(f"[dry-run] {printable}")
            return 0

        self.log.info(f"$ {printable}")
        # No capture: the simulator's output and the per-test status lines are
        # the point of running this, and buffering them would delay a regression
        # that takes minutes per test.
        return subprocess.call(cmd)

    # ── commands ──────────────────────────────────────────────────────────────

    def cmd_build(self, comp, tool, extra_vars=None):
        rc = self.ensure_makefile()
        if rc:
            return rc
        return self.make([self.build_target(comp, tool)], extra_vars)

    def cmd_run(self, comp, tool, name, variables=None):
        rc = self.ensure_makefile()
        if rc:
            return rc
        return self.make([self.run_target(comp, tool, name)], variables)

    def cmd_all(self, comp, tool, name=None, variables=None):
        """Generate, build, then run — each step serial, stopping on failure.

        JOBS is deliberately not passed to the build: parallelism applies only
        to the tests inside a group target.
        """
        target_name = name or self.default_group(comp)
        run_target  = self.run_target(comp, tool, target_name)   # validate before building

        rc = self.ensure_makefile()
        if rc:
            return rc

        build_vars = dict(variables or {})
        build_vars.pop("JOBS", None)

        self.log.header(f"[1/2] Build — {comp} ({tool})")
        rc = self.make([self.build_target(comp, tool)], build_vars)
        if rc:
            self.log.error(f"Build failed (exit {rc}); not running {target_name}.")
            return rc

        self.log.header(f"[2/2] Run — {target_name} ({tool})")
        return self.make([run_target], variables)

    def cmd_report(self):
        return self.make(["report"])

    def cmd_clean(self):
        return self.make(["clean"])
