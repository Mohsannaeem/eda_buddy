import hashlib
import os
import sys

_CHECKSUM_PREFIX = "## eda-buddy-checksum: sha256:"


def _python_exe():
    """The running interpreter, in a form the Makefile can quote.

    Forward slashes because Cygwin's bash cannot execute a backslash path, and
    cygpath in the Makefile converts this to /cygdrive/... at parse time.
    """
    return (sys.executable or "python").replace("\\", "/")


def strip_checksum(text):
    """Return `text` without its checksum line."""
    return "\n".join(l for l in text.split("\n") if not l.startswith(_CHECKSUM_PREFIX))


def compute_checksum(text):
    """Checksum of a Makefile's content, ignoring the checksum line itself."""
    return hashlib.sha256(strip_checksum(text).encode("utf-8")).hexdigest()


def read_checksum(text):
    """The checksum a Makefile claims, or None if it carries no stamp."""
    for line in text.split("\n"):
        if line.startswith(_CHECKSUM_PREFIX):
            return line[len(_CHECKSUM_PREFIX):].strip()
    return None


def is_pristine(path):
    """True when `path` is exactly as EDA Buddy generated it.

    False for a hand-edited file *and* for one generated before checksums
    existed — in both cases the content's provenance is unknown, so a caller
    about to overwrite it should preserve a copy first.
    """
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return False
    claimed = read_checksum(text)
    return claimed is not None and claimed == compute_checksum(text)


class MakefileGenerator:
    # Formats EDA Buddy can emit working dump commands for. FSDB is deliberately
    # absent: it needs the Verdi/Novas PLI, which is not configured here, so
    # emitting it would produce a command that fails at runtime.
    WAVE_FORMATS = ("vcd", "wlf")

    def __init__(self, component_configs, logger,
                 root="run", flist_subdir="filelists", makefile_subdir=".",
                 global_hooks=None, project_name=''):
        """
        component_configs : dict mapping component_name → (build_cfg, runtime_cfg)
        root              : absolute path from project_structure.yaml paths.root
        flist_subdir      : folder name for filelists, relative to root
        makefile_subdir   : folder for Makefile, relative to root ('.' = root itself)
        global_hooks      : {'pre': cmd, 'post': cmd} from project_structure.yaml
        project_name      : display name written into the report
        """
        self.configs      = component_configs
        self.log          = logger
        self.root         = root
        self.flist_dir    = os.path.join(root, flist_subdir)
        self.makefile_dir = root if makefile_subdir == '.' else os.path.join(root, makefile_subdir)
        self.global_hooks = global_hooks or {}
        self.project_name = project_name

        os.makedirs(self.flist_dir, exist_ok=True)

    # ── helpers ──────────────────────────────────────────────────────────────────

    def _posix(self, path):
        return path.replace("\\", "/")

    def _hook_lines(self, label, cmd):
        """Return Makefile recipe lines for a hook command; empty list if cmd is blank."""
        if not cmd or not str(cmd).strip():
            return []
        return [f'\t@echo "[{label}] {cmd}"', f'\t@{cmd}']

    def _make_var(self, name):
        """Component name → uppercase Make variable prefix: axi_stream_master_vip → AXI_STREAM_MASTER_VIP"""
        return name.upper()

    def _comp_build(self, name):
        return f"$({self._make_var(name)}_BUILD_DIR)"

    def _comp_work(self, name):
        return f"$({self._make_var(name)}_WORK_DIR)"

    def _comp_run(self, name):
        return f"$({self._make_var(name)}_RUN_DIR)"

    def _resolve_vars(self, flags, build_cfg):
        resolved = []
        if not flags:
            return resolved
        for flag in flags:
            if flag is None:
                continue
            f = str(flag).replace("${project.top_module}", build_cfg['project']['top_module'])
            resolved.append(f)
        return resolved

    # ── filelist generation ───────────────────────────────────────────────────────

    def _src_var(self, name):
        """Make variable name for a component's TB source root."""
        return f"{self._make_var(name)}_SRC_DIR"

    def _is_abs(self, path):
        return path.startswith('/') or (len(path) > 1 and path[1] == ':')

    def _dep_lines(self, dep_yaml_path):
        """Return filelist lines (incdir + sources) for one dependency build.yaml.
        Only interfaces and packages are pulled in — modules/dut of the dep are skipped.
        Relative paths are resolved against the dep's own root_dir."""
        import yaml as _yaml
        try:
            with open(dep_yaml_path) as f:
                dep = _yaml.safe_load(f)
        except Exception as e:
            self.log.warning(f"Could not load dep build.yaml '{dep_yaml_path}': {e}")
            return []

        dep_root = self._posix(dep.get('paths', {}).get('root_dir', ''))
        lines = [f"## -- dep: {os.path.basename(dep_yaml_path)} ({dep_root}) --"]

        for d in dep.get('compilation', {}).get('include_dirs', []):
            d = str(d)
            lines.append(f"+incdir+{d}" if self._is_abs(d) else f"+incdir+{dep_root}/{d}")

        dep_src = dep.get('compilation', {}).get('sources', {})
        for cat in ('interfaces', 'packages'):
            for src in dep_src.get(cat, []):
                src = str(src)
                lines.append(src if self._is_abs(src) else f"{dep_root}/{src}")

        return lines

    def _generate_filelist(self, name, build_cfg):
        """Generates <root>/filelists/<name>.f using ${<COMP>_SRC_DIR} so the
        filelist is portable — only the Make variable needs updating on a new machine.
        If build_cfg has dependencies.build entries, their interfaces+packages are
        prepended to the filelist (with absolute paths from the dep's root_dir)."""
        flist_path = self._posix(os.path.abspath(os.path.join(self.flist_dir, f"{name}.f")))
        self.log.info(f"Generating filelist: {flist_path}")

        # Use shell-style ${VAR} so EDA tools (vlog/vlogan/xrun) expand it at compile time.
        src_ref = f"${{{self._src_var(name)}}}"

        lines = [f"## Filelist for {name} — source root resolved from ${self._src_var(name)}"]

        # 1. Dependency build YAMLs — interfaces + packages compiled first
        dep_yamls = build_cfg.get('dependencies', {}).get('build', []) or []
        for dep_path in dep_yamls:
            lines += self._dep_lines(str(dep_path))

        # 2. This component's own include dirs and defines
        for d in build_cfg['compilation'].get('include_dirs', []):
            d = str(d)
            lines.append(f"+incdir+{d}" if self._is_abs(d) else f"+incdir+{src_ref}/{d}")
        for d in build_cfg['compilation'].get('defines', []):
            lines.append(f"+define+{d}")

        # 3. This component's own sources — dut before modules so the stub is
        #    compiled before the tb_top that instantiates it.
        sources = build_cfg['compilation']['sources']
        for category in ['interfaces', 'packages', 'dut', 'modules']:
            for src in sources.get(category, []):
                src = str(src)
                lines.append(src if self._is_abs(src) else f"{src_ref}/{src}")

        with open(flist_path, "w") as f:
            f.write("\n".join(lines))
        return flist_path

    # ── run-log shell snippet ─────────────────────────────────────────────────────

    def _run_log_snippet(self, comp, test_name):
        """
        Shell snippet that sets RUN_DIR and LOGFILE.

        When REGDIR is set (called from a group target via $(MAKE) REGDIR=...):
            RUN_DIR = REGDIR/<test_name>/          (flat layout inside the session dir)
        When REGDIR is empty (standalone single-test run):
            RUN_DIR = <root>/<comp>/run/<test>/run_<timestamp>/
        """
        base = f"{self._comp_run(comp)}/{test_name}"
        return (
            f"if [ -n \"$(REGDIR)\" ]; then "
            f"RUN_DIR=$(REGDIR)/{test_name}; "
            f"else "
            f"mkdir -p {base}; RUN_ID=$$(date +%Y%m%d_%H%M%S); RUN_DIR={base}/run_$$RUN_ID; "
            f"fi; "
            f"mkdir -p $$RUN_DIR; "
            f"LOGFILE=$$RUN_DIR/sim.log"
        )

    # ── build targets ─────────────────────────────────────────────────────────────

    def _build_targets(self, name, build, tool_settings, content):
        """
        Emits Make targets for vlib/vlog/vopt.
        Build logs → <root>/<name>/build/<tool>.log
        Work lib   → <root>/<name>/work/
        Hook order: global.pre → build.pre → job → build.post → global.post
        """
        build_dir = self._comp_build(name)
        work_lib  = self._comp_work(name)

        bh = build.get('hooks', {})
        pre_lines  = (self._hook_lines("PRE-BUILD-GLOBAL",  self.global_hooks.get('pre'))
                    + self._hook_lines("PRE-BUILD",          bh.get('pre')))
        post_lines = (self._hook_lines("POST-BUILD",         bh.get('post'))
                    + self._hook_lines("POST-BUILD-GLOBAL",  self.global_hooks.get('post')))

        # --- VCS ---
        if 'vcs' in tool_settings:
            vcs_cfg  = tool_settings['vcs']
            vcs_comp = self._resolve_vars(vcs_cfg.get('compile_flags', []), build)
            vcs_elab = self._resolve_vars(vcs_cfg.get('elaborate_flags', []), build)
            log      = f"{build_dir}/vcs.log"
            content += [f"vcs_build_{name}:"] + pre_lines + [
                f"\t@mkdir -p {build_dir}",
                f"\t@echo \"[VCS] Building {name} — log: {log}\"",
                f"\tvlogan {' '.join(vcs_comp)} -work {name}_lib -f $({name}_FLIST) 2>&1 | tee {log} && \\",
                f"\tvcs {' '.join(vcs_elab)} -o {build_dir}/simv -Mdir={build_dir}/csrc 2>&1 | tee -a {log}",
            ] + post_lines + [""]

        # --- Questa ---
        if 'questa' in tool_settings:
            questa_cfg  = tool_settings['questa']
            questa_comp = self._resolve_vars(questa_cfg.get('compile_flags', []), build)
            questa_elab = self._resolve_vars(questa_cfg.get('elaborate_flags', []), build)
            log         = f"{build_dir}/questa.log"
            content += [f"questa_build_{name}:"] + pre_lines + [
                f"\t@mkdir -p {build_dir} {work_lib}",
                f"\t@echo \"[QUESTA] Building {name} — log: {log}\"",
                f"\tvlib {work_lib} 2>&1 | tee {log} && \\",
                f"\tvlog -work {work_lib} {' '.join(questa_comp)} -f $({name}_FLIST) 2>&1 | tee -a {log} && \\",
                f"\t{' '.join(questa_elab)} -work {work_lib} 2>&1 | tee -a {log}" if questa_elab else "\t@true",
            ] + post_lines + [""]

        # --- Xcelium ---
        if 'xcelium' in tool_settings:
            xcel_cfg  = tool_settings['xcelium']
            xcel_comp = self._resolve_vars(xcel_cfg.get('compile_flags', []), build)
            xcel_elab = self._resolve_vars(xcel_cfg.get('elaborate_flags', []), build)
            log       = f"{build_dir}/xcelium.log"
            content += [f"xcelium_build_{name}:"] + pre_lines + [
                f"\t@mkdir -p {build_dir}",
                f"\t@echo \"[XCELIUM] Building {name} — log: {log}\"",
                f"\txrun {' '.join(xcel_comp)} {' '.join(xcel_elab)} -xmlibdirpath {build_dir} -f $({name}_FLIST) 2>&1 | tee {log}",
            ] + post_lines + [""]

    # ── run targets ───────────────────────────────────────────────────────────────

    def _run_targets(self, name, run_cfg, content):
        """
        Emits Make targets for vsim/simv per test.
        Sim logs → <root>/<name>/run/<test>/run_<timestamp>/sim.log
        Hook order: global.pre → run.pre → sim → run.post → global.post
        """
        work_lib  = self._comp_work(name)
        build_dir = self._comp_build(name)

        rh = run_cfg.get('hooks', {})
        run_pre_lines  = (self._hook_lines("PRE-SIM-GLOBAL",  self.global_hooks.get('pre'))
                        + self._hook_lines("PRE-SIM",          rh.get('pre')))
        run_post_lines = (self._hook_lines("POST-SIM",         rh.get('post'))
                        + self._hook_lines("POST-SIM-GLOBAL",  self.global_hooks.get('post')))

        common_args = run_cfg['runtime'].get('common_args', [])
        # Fallback matches UVM's own built-in default: a run.yaml that omits the key
        # must not go quieter than an unconfigured simulation.
        verbosity_default = "UVM_MEDIUM"
        filtered_common = []
        for arg in common_args:
            if arg.startswith("+UVM_VERBOSITY="):
                verbosity_default = arg.split("=")[1]
            else:
                filtered_common.append(arg)
        common_run_args = " ".join(filtered_common)

        # +UVM_VERBOSITY is lifted out of common_args into a Make variable so a single
        # run can be re-run louder without editing the yaml:
        #   make questa_run_<comp>_<test> VERBOSITY=UVM_FULL
        # A bare VERBOSITY= on the command line wins over the per-component default.
        MV = self._make_var(name)
        content += [
            "## -- Runtime verbosity (default from run.yaml; override: make ... VERBOSITY=UVM_HIGH) --",
            f"{MV}_VERBOSITY ?= {verbosity_default}",
            "",
        ]
        verbosity_arg = f"+UVM_VERBOSITY=$(if $(VERBOSITY),$(VERBOSITY),$({MV}_VERBOSITY))"

        # The per-run output dir is handed to the simulation so testbench code can write
        # its own artifacts (trackers, custom dumps) alongside sim.log and waves.vcd
        # instead of into the cwd, where every test would overwrite the previous one.
        run_dir_arg = "+RUN_DIR=$$RUN_DIR"

        tool_args   = run_cfg['runtime'].get('tool_args', {})
        vcs_args    = " ".join(tool_args.get('vcs', []))
        xcel_args   = " ".join(tool_args.get('xcelium', []))
        # Strip mode/do flags from questa tool_args — the Makefile template manages
        # -batch/-gui via $MODE and -do via $DO_CMD; duplicates cause vsim-3905.
        _questa_raw = tool_args.get('questa', [])
        _questa_filtered = []
        _skip_next = False
        for _a in _questa_raw:
            if _skip_next:
                _skip_next = False
                continue
            if _a in ('-batch', '-c', '-gui', '-i'):
                continue
            if _a == '-do':
                _skip_next = True   # skip the next token (the do-command string)
                continue
            if str(_a).startswith("-do "):
                continue
            _questa_filtered.append(str(_a))
        questa_args = " ".join(_questa_filtered)

        # runtime.debug supplies the per-component defaults. Each becomes a Make
        # variable so a single run can override it without editing the YAML, the
        # same way VERBOSITY works:
        #   make questa_run_<comp>_<test> WAVES=1 GUI=1 QUIET=0 WAVE_FORMAT=wlf
        debug = run_cfg['runtime'].get('debug', {}) or {}

        def _flag(key, default=False):
            return "1" if debug.get(key, default) else "0"

        wave_format = str(debug.get('wave_format', 'vcd')).lower()
        if wave_format not in self.WAVE_FORMATS:
            raise ValueError(
                "component '{}': wave_format '{}' is not supported. Use one of: {}.\n"
                "  vcd  — portable, works with Questa and VCS\n"
                "  wlf  — Questa's native format, no extra libraries\n"
                "FSDB needs the Verdi/Novas PLI, which EDA Buddy does not configure; "
                "if you need it, add the -pli flags to runtime.tool_args yourself."
                .format(name, wave_format, ", ".join(sorted(self.WAVE_FORMATS)))
            )

        content += [
            "## -- Runtime debug defaults (from run.yaml; override on the make line) --",
            f"{MV}_WAVES       ?= {_flag('dump_waves')}",
            f"{MV}_GUI         ?= {_flag('gui_mode')}",
            f"{MV}_QUIET       ?= {_flag('quiet_sim')}",
            f"{MV}_WAVE_FORMAT ?= {wave_format}",
            "",
        ]

        # A bare WAVES=/GUI=/QUIET= on the command line wins over the component default.
        waves_eff  = f"$(if $(WAVES),$(WAVES),$({MV}_WAVES))"
        gui_eff    = f"$(if $(GUI),$(GUI),$({MV}_GUI))"
        quiet_eff  = f"$(if $(QUIET),$(QUIET),$({MV}_QUIET))"
        format_eff = f"$(if $(WAVE_FORMAT),$(WAVE_FORMAT),$({MV}_WAVE_FORMAT))"

        # quiet=1 → redirect only to the log, no terminal noise
        # quiet=0 → tee to terminal AND log (default)
        # Resolved by make rather than by the shell, because a pipeline cannot be
        # applied conditionally inline without repeating the whole command.
        content += [
            # $$LOGFILE, not $$$$: a recursive variable's value is expanded once
            # when used, exactly like inline recipe text, so $$ -> $ for the shell.
            f"{MV}_REDIRECT = $(if $(filter 1,{quiet_eff}),> $$LOGFILE 2>&1,2>&1 | tee $$LOGFILE)",
            "",
        ]
        _redirect = f"$({MV}_REDIRECT)"

        total_tests = len(run_cfg['test_config']['entry_points'])

        for test in run_cfg['test_config']['entry_points']:
            t_name   = test['name']
            t_seed   = test['seed']
            t_args   = " ".join(test.get('user_args', []))
            snippet  = self._run_log_snippet(name, t_name)

            # Inline status line: capture sim exit, print one-liner with session totals, re-exit.
            # TOTAL is set by the group target to its own test count (e.g. 7 for smoke_test,
            # 35 for regression). Falls back to all entry_points when run standalone.
            # If run_report.py is missing, print a bold warning and skip — sim result is unaffected.
            _report_cmd = (f"$(REPORT_CMD) --single-log $$LOGFILE --test-name {t_name} "
                           f"--total $(if $(TOTAL),$(TOTAL),{total_tests}) "
                           f"$(if $(REGDIR),--comp-run-dir $(REGDIR),) "
                           f"$(RMS_PROGRESS_ARG) $(RMS_URL_ARG)")
            _skip_msg   = r"printf '\033[1m[EDA Buddy] Per-test report skipped: eda_buddy not importable. Run: pip install -e <path-to-eda_buddy>\033[0m\n'"
            _sl = (f"SIM_RC=$$?; "
                   f"if [ -n \"$(REPORT_OK)\" ]; then {_report_cmd}; else {_skip_msg}; fi; "
                   f"exit $$SIM_RC")

            # VCS
            content += [f"vcs_run_{name}_{t_name}:"] + run_pre_lines + [
                f"\t@{snippet}; \\",
                f"\t echo \"[VCS] Running {t_name}\"; \\",
                f"\t VCD_ARGS=\"\"; if [ \"{waves_eff}\" = \"1\" ]; then VCD_ARGS=\"+vcs+dumpvars+$$RUN_DIR/waves.vcd\"; fi; \\",
                f"\t GUI_FLAG=\"\"; if [ \"{gui_eff}\" = \"1\" ]; then GUI_FLAG=\"-gui\"; fi; \\",
                f"\t {build_dir}/simv $$GUI_FLAG $$VCD_ARGS +UVM_TESTNAME={t_name} +ntb_random_seed={t_seed} {verbosity_arg} {run_dir_arg} {common_run_args} {vcs_args} {t_args} {_redirect}; {_sl}",
            ] + run_post_lines + [""]

            # Questa
            content += [f"questa_run_{name}_{t_name}:"] + run_pre_lines + [
                f"\t@{snippet}; \\",
                f"\t echo \"[QUESTA] Running {t_name}\"; \\",
                f"\t DO_CMD=\"run -all; quit\"; WLF_ARGS=\"\"; \\",
                # WLF is Questa's native database: it needs -wlf for the destination
                # and `log -r /*` to actually record signals, not a vcd-style dump.
                f"\t if [ \"{waves_eff}\" = \"1\" ]; then \\",
                f"\t   if [ \"{format_eff}\" = \"wlf\" ]; then \\",
                f"\t     WLF_ARGS=\"-wlf $$RUN_DIR/waves.wlf\"; DO_CMD=\"log -r /*; run -all; quit\"; \\",
                f"\t   else \\",
                f"\t     DO_CMD=\"vcd file $$RUN_DIR/waves.vcd; vcd add -r /*; run -all; quit\"; \\",
                f"\t   fi; \\",
                f"\t fi; \\",
                f"\t if [ \"{gui_eff}\" = \"1\" ]; then DO_CMD=\"add wave -r /*; run -all\"; fi; \\",
                f"\t MODE=\"-batch\"; if [ \"{gui_eff}\" = \"1\" ]; then MODE=\"-gui\"; fi; \\",
                f"\t vsim $$MODE $$WLF_ARGS -do \"$$DO_CMD\" {questa_args} -lib {work_lib} db_opt +UVM_TESTNAME={t_name} -sv_seed {t_seed} {verbosity_arg} {run_dir_arg} {common_run_args} {t_args} {_redirect}; {_sl}",
            ] + run_post_lines + [""]

            # Xcelium
            content += [f"xcelium_run_{name}_{t_name}:"] + run_pre_lines + [
                f"\t@{snippet}; \\",
                f"\t echo \"[XCELIUM] Running {t_name}\"; \\",
                f"\t {build_dir}/simv +UVM_TESTNAME={t_name} -svseed {t_seed} {verbosity_arg} {run_dir_arg} {common_run_args} {xcel_args} {t_args} {_redirect}; {_sl}",
            ] + run_post_lines + [""]

        # Group targets — create a timestamped session dir, run all tests, then report
        groups        = run_cfg.get('groups', {})
        proj_name_arg = f'--project-name "{self.project_name}"' if self.project_name else ''

        for g_name, g_val in groups.items():
            # Support two group formats:
            #   list (old):  groups: { smoke_test: [tc_001, tc_002] }
            #   dict (new):  groups: { smoke_test: { tests: [...], hooks: {pre: '', post: ''} } }
            #   legacy dict: groups: { smoke_test: { tests: [...], regression_post_hook: "..." } }
            if isinstance(g_val, list):
                tests      = g_val
                pre_hook   = ''
                post_hook  = ''
                rms_id     = ''
            else:
                tests     = g_val.get('tests', [])
                grp_hooks = g_val.get('hooks', {}) or {}
                pre_hook  = (grp_hooks.get('pre') or '').strip()
                # hooks.post takes precedence; fall back to legacy regression_post_hook key
                post_hook = (grp_hooks.get('post') or g_val.get('regression_post_hook') or '').strip()
                # rms_id replaces hand-written push_result hooks: no interpreter
                # path, no shell quoting, no {total}/{passed}/{failed} templating.
                rms_id    = str(g_val.get('rms_id') or '').strip()

            if not tests:
                continue

            post_cmd_arg  = f'--post-cmd "{post_hook}"' if post_hook else ''
            # Publishing is opt-in: rms_id only names the target regression, and
            # nothing is pushed unless PUSH_RESULTS=1 (eda-buddy --push-results).
            # RMS_ID overrides which regression receives it.
            _effective_id = f'$(if $(RMS_ID),$(RMS_ID),{rms_id})' if rms_id else '$(RMS_ID)'
            rms_id_arg    = (f'$(if $(filter 1,$(PUSH_RESULTS)),'
                             f'$(if {_effective_id},--rms-id {_effective_id},),)')
            pre_hook_lines = self._hook_lines(f"PRE-{g_name.upper()}", pre_hook)

            for tool in ('vcs', 'questa', 'xcelium'):
                test_targets = ' '.join(f'{tool}_run_{name}_{t}' for t in tests)
                tests_csv    = ','.join(tests)
                # All steps chained in one shell so $$REGDIR persists across them.
                # $(MAKE) -k keeps going on failures; || true lets the chain continue.
                report_args = (f'--root $(ROOT) --component {name} {proj_name_arg} '
                               f'--tests {tests_csv} --reg-dir $$REGDIR --save-to $$REGDIR/report.txt '
                               f'{post_cmd_arg} {rms_id_arg} $(RMS_URL_ARG)')
                content += [
                    f".PHONY: {tool}_run_{name}_{g_name}",
                    f"{tool}_run_{name}_{g_name}:",
                ] + pre_hook_lines + [
                    f"\t@REGDIR=\"{self._comp_run(name)}/{g_name}_$$(date +%Y%m%d_%H%M%S)\"; \\",
                    f"\tmkdir -p \"$$REGDIR\"; \\",
                    f"\techo \"[{g_name.upper()}] Session dir: $$REGDIR  Total={len(tests)} tests\"; \\",
                    # RMS_ID is passed down explicitly: the per-test targets are
                    # shared by every group, so which regression a test's progress
                    # snapshot belongs to is only known here, at the group.
                    f"\t$(MAKE) -k $(PARALLEL_FLAGS) REGDIR=$$REGDIR TOTAL={len(tests)} "
                    f"RMS_ID=\"{_effective_id}\" {test_targets} || true; \\",
                    f"\techo \"\"; \\",
                    f"\tif [ -n \"$(REPORT_OK)\" ]; then $(REPORT_CMD) {report_args}; "
                    r"else printf '\033[1m[EDA Buddy] Final report skipped: eda_buddy not importable. Run: pip install -e <path-to-eda_buddy>\033[0m\n'; fi",
                    "",
                ]

    # ── top-level generate ────────────────────────────────────────────────────────

    def generate(self):
        """Write the Makefile to <root>/Makefile (or paths.makefile sub-path)."""
        output_path = os.path.join(self.makefile_dir, "Makefile")
        os.makedirs(self.makefile_dir, exist_ok=True)
        self.log.info(f"Generating Makefile at {output_path}")

        root_posix = self._posix(self.root)

        # Header + tool settings
        content = [
            "## AUTO-GENERATED BY EDA BUDDY — edit variables below to relocate the project",
            "",
            "SHELL      := /bin/bash",
            ".SHELLFLAGS := -o pipefail -c",
            "",
            "## ======================================================",
            "## PATHS — all paths derived from ROOT; override ROOT or",
            "##          individual variables to relocate the project",
            "## ======================================================",
            f"ROOT      := {root_posix}",
            f"FLIST_DIR := $(ROOT)/filelists",
            "",
            "## -- EDA Buddy's own entry points --",
            "## Invoked as modules rather than file paths: an installed package moves",
            "## between venvs and site-packages, and a baked-in absolute path goes stale",
            "## the first time it does. This form keeps working across reinstalls.",
            "",
            "## Pinned to the interpreter that generated this file. A bare `python`",
            "## resolves to Cygwin's own /usr/bin/python under Cygwin make — a separate",
            "## installation, without eda_buddy — which silently disables all reporting.",
            "## Written in mixed form (C:/...): Cygwin executes that directly, whereas a",
            "## backslash path fails, and cygpath is not usable here because the one",
            "## first on PATH may be MSYS's, which emits /c/... that Cygwin cannot resolve.",
            f"PYTHON     ?= {_python_exe()}",
            "REPORT_CMD := $(PYTHON) -m eda_buddy.run_report",
            "PUSH_CMD   := $(PYTHON) -m eda_buddy.push_result",
            "",
            "## Probe once at parse time: empty when EDA Buddy is not importable.",
            "REPORT_OK  := $(shell $(PYTHON) -c \"import eda_buddy.run_report\" 2>/dev/null && echo 1)",
            "",
            "## NOTE: If EDA Buddy is not importable, reporting/RMS steps are silently",
            "## skipped with a bold warning — builds and simulations continue.",
            "",
            "## -- Parallel test execution within a regression group --",
            "## Serial by default. `make <group target> JOBS=4` runs that group's tests",
            "## concurrently; --output-sync keeps each test's output as one block instead",
            "## of interleaving. Only the group's inner sub-make is affected — generation",
            "## and build are always serial.",
            "JOBS ?=",
            "PARALLEL_FLAGS := $(if $(JOBS),-j$(JOBS) --output-sync=target,)",
            "",
            "## -- Publishing results to the RMS --",
            "## Opt-in. A group's rms_id only names the regression to push to;",
            "## PUSH_RESULTS=1 is what actually publishes, so an ordinary local run",
            "## never writes to the shared dashboard. RMS_ID overrides the target.",
            "PUSH_RESULTS ?= 0",
            "RMS_ID ?=",
            "## Empty = http://localhost:8000 (or $RMS_URL from the environment).",
            "RMS_URL ?=",
            "",
            "## Progress reporting: push a snapshot row after every test finishes,",
            "## so the dashboard advances during a long regression instead of only",
            "## at the end. RMS_PROGRESS=0 leaves just the single final push.",
            "RMS_PROGRESS ?= 1",
            "",
            "## Recursive (=) not simple (:=): RMS_ID is handed to the per-test",
            "## sub-make by the group target, so these must expand where they are used.",
            "RMS_URL_ARG      = $(if $(RMS_URL),--rms-url $(RMS_URL),)",
            "RMS_PROGRESS_ARG = $(if $(filter 1,$(PUSH_RESULTS)),"
            "$(if $(filter 1,$(RMS_PROGRESS)),$(if $(RMS_ID),--rms-id $(RMS_ID),),),)",
            "",
        ]

        # Per-component path variables block
        content += ["## -- Component path variables (build/work/run derive from ROOT) --"]
        for name, (build, _) in self.configs.items():
            MV      = self._make_var(name)
            src_dir = self._posix(build['paths']['root_dir'])
            content += [
                f"{MV}_BUILD_DIR := $(ROOT)/{name}/build",
                f"{MV}_WORK_DIR  := $(ROOT)/{name}/work",
                f"{MV}_RUN_DIR   := $(ROOT)/{name}/run",
                f"{MV}_SRC_DIR   := {src_dir}",
            ]
        content += [""]

        # Export SRC_DIR vars so EDA tools can expand ${VAR} inside .f files
        content += ["## -- Export source dirs so EDA tools expand them in filelists --"]
        for name in self.configs:
            content.append(f"export {self._src_var(name)}")
        content += [""]

        for name, (build, run) in self.configs.items():
            self._generate_filelist(name, build)   # writes the .f file to disk

            content += [
                f"## {'='*54}",
                f"## COMPONENT: {name}",
                f"## {'='*54}",
                f"{name}_FLIST := $(FLIST_DIR)/{name}.f",
                f"{name}_TOP   := {build['project']['top_module']}",
                "",
            ]
            self._build_targets(name, build, build.get('tool_settings', {}), content)
            self._run_targets(name, run, content)

        proj_name_arg = f'--project-name "{self.project_name}" ' if self.project_name else ''
        content += [
            "## ======================================================",
            "## UTILITIES",
            "## ======================================================",
            ".PHONY: clean report",
            "clean:",
            "\trm -rf $(ROOT)/*/build $(ROOT)/*/work $(ROOT)/*/run",
            "\t@echo 'Cleaned per-component build/work/run dirs. Filelists and Makefile preserved.'",
            "",
            "## REPORT — scan all run logs and print pass/fail summary",
            "report:",
            f"\t@if [ -n \"$(REPORT_OK)\" ]; then $(REPORT_CMD) --root $(ROOT) {proj_name_arg}--save-to $(ROOT)/logs/report.txt; "
            r"else printf '\033[1m[EDA Buddy] Report skipped: eda_buddy not importable. Run: pip install -e <path-to-eda_buddy>\033[0m\n'; fi",
            "",
        ]

        # Stamped so `eda-buddy build/run` can tell a pristine Makefile from a
        # hand-edited one and back the latter up before regenerating it.
        body = "\n".join(content)
        body = body.replace("\n", "\n{}{}\n".format(_CHECKSUM_PREFIX, compute_checksum(body)), 1)

        with open(output_path, "w") as f:
            f.write(body)

        self.log.success(f"Makefile ready at {output_path}")
