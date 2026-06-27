import os
import sys
import yaml
import argparse

# Modular Imports
from scripts.logger import EDABuddyLogger
from scripts.makefile_gen import MakefileGenerator

def main():
    parser = argparse.ArgumentParser(
        description="EDA Buddy - Centralized Project Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("--gen-makefile", action="store_true", help="Generate Hierarchical Makefile under paths.root")
    parser.add_argument("--project-cfg", default="project_structure.yaml", help="Path to project structure YAML")

    args = parser.parse_args()

    try:
        # 1. Load Project Structure (paths.root drives everything)
        if not os.path.exists(args.project_cfg):
            print(f"[ERROR] Project structure file '{args.project_cfg}' not found.")
            sys.exit(1)

        with open(args.project_cfg, 'r') as f:
            project_data = yaml.safe_load(f)

        # 2. Resolve root, sub-paths, and global hooks from YAML
        proj_paths   = project_data.get('paths', {})
        root         = os.path.abspath(proj_paths.get('root', 'run'))
        flist_sub    = proj_paths.get('filelists', 'filelists')
        makefile_sub = proj_paths.get('makefile', '.')
        global_hooks = project_data.get('hooks', {})
        os.makedirs(root, exist_ok=True)

        log = EDABuddyLogger(log_dir=os.path.join(root, "logs", "eda_buddy"))
        log.header("EDA Buddy Project Loading")
        log.info(f"Project : {project_data.get('project_name', 'Unknown')}")
        log.info(f"Root    : {root}")
        log.info(f"Filelist: {os.path.join(root, flist_sub)}")

        # 3. Load Component Configs
        component_configs = {}
        for comp in project_data.get('components', []):
            name   = comp['name']
            b_path = comp['build_cfg']
            r_path = comp['runtime_cfg']

            if os.path.exists(b_path) and os.path.exists(r_path):
                log.info(f"Loading Component: {name}")
                with open(b_path, 'r') as bf, open(r_path, 'r') as rf:
                    component_configs[name] = (yaml.safe_load(bf), yaml.safe_load(rf))
            else:
                log.warning(f"Config files for '{name}' not found:")
                if not os.path.exists(b_path):
                    log.warning(f"  build_cfg missing   : {b_path}")
                if not os.path.exists(r_path):
                    log.warning(f"  runtime_cfg missing : {r_path}")

        if not component_configs:
            log.error("No valid component configurations were loaded.")
            sys.exit(1)

        # 4. Action Dispatching
        if args.gen_makefile:
            gen = MakefileGenerator(
                component_configs,
                log,
                root=root,
                flist_subdir=flist_sub,
                makefile_subdir=makefile_sub,
                global_hooks=global_hooks,
                project_name=project_data.get('project_name', ''),
            )
            gen.generate()

    except Exception as e:
        print(f"[ERROR] Critical tool failure: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
