"""Loading and interpretation of project_structure.yaml.

Extracted from cli.main() so the generator and the build/run driver read the
project exactly the same way. The log lines emitted here are deliberately
identical to the pre-CLI versions.
"""

import os
import sys

import yaml

from .logger import EDABuddyLogger


class ProjectError(Exception):
    """Raised for a malformed or unusable project configuration."""


class Project:
    """A loaded project_structure.yaml plus every component's build/run YAML."""

    def __init__(self, cfg_path, data, log):
        self.cfg_path = os.path.abspath(cfg_path)
        self.data     = data
        self.log      = log

        paths              = data.get('paths', {})
        self.root          = os.path.abspath(paths.get('root', 'run'))
        self.flist_subdir  = paths.get('filelists', 'filelists')
        self.makefile_sub  = paths.get('makefile', '.')
        self.global_hooks  = data.get('hooks', {})
        self.name          = data.get('project_name', '')

        # `tools:` is optional; a project file without it behaves exactly as before.
        tools              = data.get('tools', {}) or {}
        self.make_exe      = tools.get('make') or None
        self.default_tool  = tools.get('simulator') or None

        self.components    = {}   # name -> (build_cfg, run_cfg)
        self._cfg_files    = [self.cfg_path]   # every YAML that feeds generation

    @property
    def makefile_dir(self):
        return self.root if self.makefile_sub == '.' else os.path.join(self.root, self.makefile_sub)

    @property
    def makefile_path(self):
        return os.path.join(self.makefile_dir, "Makefile")

    def config_files(self):
        """Every YAML whose edit should invalidate the generated Makefile.

        Includes dependency build YAMLs, since a dependency's interfaces and
        packages are inlined into the dependent component's filelist.
        """
        files = list(self._cfg_files)
        for build_cfg, _ in self.components.values():
            for dep in (build_cfg.get('dependencies', {}) or {}).get('build', []) or []:
                files.append(str(dep))
        return [f for f in files if os.path.exists(f)]

    def component(self, name):
        if name not in self.components:
            raise ProjectError(
                "unknown component '{}'. Declared: {}".format(
                    name, ", ".join(sorted(self.components)) or "(none)"))
        return self.components[name]

    def resolve_component(self, name=None):
        """Return a component name, defaulting only when the choice is unambiguous."""
        if name:
            self.component(name)      # validates
            return name
        if len(self.components) == 1:
            return next(iter(self.components))
        raise ProjectError(
            "project declares {} components, so --comp is required. Choose one of: {}".format(
                len(self.components), ", ".join(sorted(self.components))))


def load_project(cfg_path):
    """Read project_structure.yaml and every referenced component YAML."""
    if not os.path.exists(cfg_path):
        print(f"[ERROR] Project structure file '{cfg_path}' not found.")
        sys.exit(1)

    with open(cfg_path, 'r') as f:
        data = yaml.safe_load(f)

    paths = (data or {}).get('paths', {})
    root  = os.path.abspath(paths.get('root', 'run'))
    os.makedirs(root, exist_ok=True)

    log = EDABuddyLogger(log_dir=os.path.join(root, "logs", "eda_buddy"))
    project = Project(cfg_path, data, log)

    log.header("EDA Buddy Project Loading")
    log.info(f"Project : {project.name or 'Unknown'}")
    log.info(f"Root    : {project.root}")
    log.info(f"Filelist: {os.path.join(project.root, project.flist_subdir)}")

    for comp in (data.get('components', []) or []):
        name   = comp['name']
        b_path = comp['build_cfg']
        r_path = comp['runtime_cfg']

        if os.path.exists(b_path) and os.path.exists(r_path):
            log.info(f"Loading Component: {name}")
            with open(b_path, 'r') as bf, open(r_path, 'r') as rf:
                project.components[name] = (yaml.safe_load(bf), yaml.safe_load(rf))
            project._cfg_files += [b_path, r_path]
        else:
            log.warning(f"Config files for '{name}' not found:")
            if not os.path.exists(b_path):
                log.warning(f"  build_cfg missing   : {b_path}")
            if not os.path.exists(r_path):
                log.warning(f"  runtime_cfg missing : {r_path}")

    if not project.components:
        log.error("No valid component configurations were loaded.")
        sys.exit(1)

    return project
