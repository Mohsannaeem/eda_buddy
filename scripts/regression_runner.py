import subprocess

class RegressionRunner:
    def __init__(self, runtime_cfg, logger):
        self.runtime = runtime_cfg
        self.log = logger

    def run_all(self, group_name="regression"):
        self.log.header(f"Starting Regression Group: {group_name}")
        tests = self.runtime.get('groups', {}).get(group_name, [])
        
        if not tests:
            self.log.error(f"No tests found in group '{group_name}'")
            return

        results = []
        for test in tests:
            self.log.info(f"Launching Test: {test}")
            # Mocking for now - in production this would call the generated Makefile
            # subprocess.run(["make", "run", f"TEST_NAME={test}"])
            results.append((test, "NOT_RUN (Dry Run)"))
        
        self.log.header("Regression Summary")
        for test, status in results:
            self.log.info(f"{test.ljust(40)} : {status}")
