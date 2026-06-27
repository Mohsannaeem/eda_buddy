import os

_SCRIPTS_DIR   = os.path.dirname(os.path.abspath(__file__)).replace('\\', '/')
_REPORT_SCRIPT = f"{_SCRIPTS_DIR}/run_report.py"
_PUSH_SCRIPT   = f"{_SCRIPTS_DIR}/push_result.py"


class MakefileGenerator:
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

    def _generate_filelist(self, name, build_cfg):
        """Generates <root>/filelists/<name>.f using ${<COMP>_SRC_DIR} so the
        filelist is portable — only the Make variable needs updating on a new machine."""
        flist_path = self._posix(os.path.abspath(os.path.join(self.flist_dir, f"{name}.f")))
        self.log.info(f"Generating filelist: {flist_path}")

        # Use shell-style ${VAR} so EDA tools (vlog/vlogan/xrun) expand it at compile time.
        src_ref = f"${{{self._src_var(name)}}}"

        lines = [f"## Filelist for {name} — source root resolved from ${self._src_var(name)}"]
        for d in build_cfg['compilation'].get('include_dirs', []):
            lines.append(f"+incdir+{src_ref}/{d}")
        for d in build_cfg['compilation'].get('defines', []):
            lines.append(f"+define+{d}")

        sources = build_cfg['compilation']['sources']
        for category in ['interfaces', 'packages', 'modules', 'dut']:
            for src in sources.get(category, []):
                if src.startswith('/') or (len(src) > 1 and src[1] == ':'):
                    lines.append(src)   # already absolute — keep as-is
                else:
                    lines.append(f"{src_ref}/{src}")

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
        verbosity_default = "UVM_LOW"
        filtered_common = []
        for arg in common_args:
            if arg.startswith("+UVM_VERBOSITY="):
                verbosity_default = arg.split("=")[1]
            else:
                filtered_common.append(arg)
        common_run_args = " ".join(filtered_common)

        tool_args   = run_cfg['runtime'].get('tool_args', {})
        vcs_args    = " ".join(tool_args.get('vcs', []))
        questa_args = " ".join(tool_args.get('questa', []))
        xcel_args   = " ".join(tool_args.get('xcelium', []))

        quiet_sim = run_cfg['runtime'].get('debug', {}).get('quiet_sim', False)
        # quiet_sim=true  → redirect only to log, no terminal noise
        # quiet_sim=false → tee to terminal AND log (default)
        _redirect = "> $$LOGFILE 2>&1" if quiet_sim else "2>&1 | tee $$LOGFILE"

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
            _report_cmd = (f"python $(REPORT_SCRIPT) --single-log $$LOGFILE --test-name {t_name} "
                           f"--total $(if $(TOTAL),$(TOTAL),{total_tests}) "
                           f"$(if $(REGDIR),--comp-run-dir $(REGDIR),)")
            _skip_msg   = r"printf '\033[1m[EDA Buddy] Per-test report skipped: run_report.py not found. Check EDA Buddy repository.\033[0m\n'"
            _sl = (f"SIM_RC=$$?; "
                   f"if [ -f \"$(REPORT_SCRIPT)\" ]; then {_report_cmd}; else {_skip_msg}; fi; "
                   f"exit $$SIM_RC")

            # VCS
            content += [f"vcs_run_{name}_{t_name}:"] + run_pre_lines + [
                f"\t@{snippet}; \\",
                f"\t echo \"[VCS] Running {t_name}\"; \\",
                f"\t VCD_ARGS=\"\"; if [ \"$(WAVES)\" = \"1\" ]; then VCD_ARGS=\"+vcs+dumpvars+$$RUN_DIR/waves.vcd\"; fi; \\",
                f"\t GUI_FLAG=\"\"; if [ \"$(GUI)\" = \"1\" ]; then GUI_FLAG=\"-gui\"; fi; \\",
                f"\t {build_dir}/simv $$GUI_FLAG $$VCD_ARGS +UVM_TESTNAME={t_name} +ntb_random_seed={t_seed} {common_run_args} {vcs_args} {t_args} {_redirect}; {_sl}",
            ] + run_post_lines + [""]

            # Questa
            content += [f"questa_run_{name}_{t_name}:"] + run_pre_lines + [
                f"\t@{snippet}; \\",
                f"\t echo \"[QUESTA] Running {t_name}\"; \\",
                f"\t DO_CMD=\"run -all; quit\"; \\",
                f"\t if [ \"$(WAVES)\" = \"1\" ]; then DO_CMD=\"vcd file $$RUN_DIR/waves.vcd; vcd add -r /*; run -all; quit\"; fi; \\",
                f"\t if [ \"$(GUI)\" = \"1\" ]; then DO_CMD=\"add wave -r /*; run -all\"; fi; \\",
                f"\t MODE=\"-c -batch\"; if [ \"$(GUI)\" = \"1\" ]; then MODE=\"-gui\"; fi; \\",
                f"\t vsim $$MODE -do \"$$DO_CMD\" {questa_args} -lib {work_lib} db_opt +UVM_TESTNAME={t_name} -sv_seed {t_seed} {common_run_args} {t_args} {_redirect}; {_sl}",
            ] + run_post_lines + [""]

            # Xcelium
            content += [f"xcelium_run_{name}_{t_name}:"] + run_pre_lines + [
                f"\t@{snippet}; \\",
                f"\t echo \"[XCELIUM] Running {t_name}\"; \\",
                f"\t {build_dir}/simv +UVM_TESTNAME={t_name} -svseed {t_seed} {common_run_args} {xcel_args} {t_args} {_redirect}; {_sl}",
            ] + run_post_lines + [""]

        # Group targets — create a timestamped session dir, run all tests, then report
        groups        = run_cfg.get('groups', {})
        proj_name_arg = f'--project-name "{self.project_name}"' if self.project_name else ''

        for g_name, g_val in groups.items():
            # Support two group formats:
            #   list (old): groups: { smoke_test: [tc_001, tc_002] }
            #   dict (new): groups: { smoke_test: { tests: [...], regression_post_hook: "..." } }
            if isinstance(g_val, list):
                tests      = g_val
                group_hook = ''
            else:
                tests      = g_val.get('tests', [])
                group_hook = (g_val.get('regression_post_hook') or '').strip()

            if not tests:
                continue

            post_cmd_arg = f'--post-cmd "{group_hook}"' if group_hook else ''

            for tool in ('vcs', 'questa', 'xcelium'):
                test_targets = ' '.join(f'{tool}_run_{name}_{t}' for t in tests)
                tests_csv    = ','.join(tests)
                # All steps chained in one shell so $$REGDIR persists across them.
                # $(MAKE) -k keeps going on failures; || true lets the chain continue.
                report_args = (f'--root $(ROOT) --component {name} {proj_name_arg} '
                               f'--tests {tests_csv} --reg-dir $$REGDIR --save-to $$REGDIR/report.txt '
                               f'{post_cmd_arg}')
                content += [
                    f".PHONY: {tool}_run_{name}_{g_name}",
                    f"{tool}_run_{name}_{g_name}:",
                    f"\t@REGDIR=\"{self._comp_run(name)}/{g_name}_$$(date +%Y%m%d_%H%M%S)\"; \\",
                    f"\tmkdir -p \"$$REGDIR\"; \\",
                    f"\techo \"[{g_name.upper()}] Session dir: $$REGDIR  Total={len(tests)} tests\"; \\",
                    f"\t$(MAKE) -k REGDIR=$$REGDIR TOTAL={len(tests)} {test_targets} || true; \\",
                    f"\techo \"\"; \\",
                    f"\tif [ -f \"$(REPORT_SCRIPT)\" ]; then python $(REPORT_SCRIPT) {report_args}; "
                    r"else printf '\033[1m[EDA Buddy] Final report skipped: run_report.py not found. Check EDA Buddy repository.\033[0m\n'; fi",
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
            # cygpath converts D:/... to /cygdrive/d/... for Cygwin bash; fallback keeps original
            f"REPORT_SCRIPT := $(shell cygpath -u \"{_REPORT_SCRIPT}\" 2>/dev/null || echo \"{_REPORT_SCRIPT}\")",
            f"PUSH_SCRIPT   := $(shell cygpath -u \"{_PUSH_SCRIPT}\"   2>/dev/null || echo \"{_PUSH_SCRIPT}\")",
            "",
            "## NOTE: If REPORT_SCRIPT or PUSH_SCRIPT are missing, reporting/RMS steps",
            "## are silently skipped with a bold warning — builds and simulations continue.",
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
            f"\t@if [ -f \"$(REPORT_SCRIPT)\" ]; then python $(REPORT_SCRIPT) --root $(ROOT) {proj_name_arg}--save-to $(ROOT)/logs/report.txt; "
            r"else printf '\033[1m[EDA Buddy] Report skipped: run_report.py not found. Check EDA Buddy repository.\033[0m\n'; fi",
            "",
        ]

        with open(output_path, "w") as f:
            f.write("\n".join(content))

        self.log.success(f"Makefile ready at {output_path}")
