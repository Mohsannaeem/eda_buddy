"""
push_result.py — Push a regression result to the RMS (Result Management System).

The regression ID must already exist in the RMS GUI before you can push results
to it. This script only adds a new row to run_results — it never creates a new
regression.

Usage:
  python -m eda_buddy.push_result --id MY_REGRESSION --total 46 --passed 44 \
                                  --failed 1 --timeout 1

Optional flags:
  --timeout Tests that hit their time limit (default: 0)
  --status  Explicit run status: passed|failed|build_fail|timeout|aborted
            (default: derived by the server from the counts). Use build_fail
            when the build did not compile and no tests ran.
  --url     RMS backend base URL  (default: $RMS_URL or http://localhost:8000)
  --start   Start time ISO string (default: server sets to now)
  --end     End time ISO string   (default: server sets to now)
  --log     Path to regression log file (the browsable "View Log" link)
  --machine-log  On-agent logs path, shown as the "Logs path" row in the result
                 email. Display only — it does not affect "View Log".

If the RMS server is unreachable the script exits cleanly with a warning so
the regression Makefile flow is NOT interrupted.
"""

import argparse
import os
import sys

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


STATUSES = ("passed", "failed", "build_fail", "timeout", "aborted")


def parse_args():
    p = argparse.ArgumentParser(description="Push a result entry to the RMS.")
    p.add_argument("--id",      required=True,  help="Regression ID (must exist in the RMS GUI)")
    p.add_argument("--total",   required=True,  type=int, help="Total number of tests")
    p.add_argument("--passed",  required=True,  type=int, help="Number of tests that passed")
    p.add_argument("--failed",  required=True,  type=int, help="Number of tests that failed")
    p.add_argument("--timeout", default=0, type=int, help="Tests that hit their time limit")
    p.add_argument("--status",  default=None, choices=list(STATUSES),
                   help="Explicit run status (default: derived from counts)")
    p.add_argument("--url",     default=None, help="RMS backend base URL")
    p.add_argument("--start",   default=None,   help="Start time (ISO 8601)")
    p.add_argument("--end",     default=None,   help="End time (ISO 8601)")
    p.add_argument("--log",     default=None,   help="Path to the regression log file")
    p.add_argument("--machine-log", dest="machine_log", default=None,
                   help="On-agent logs path (email 'Logs path' row only)")
    return p.parse_args()


def _warn_rms_unavailable(url, reason):
    print(f"")
    print(f"[RMS] WARNING: Result was NOT pushed.")
    print(f"[RMS] Reason : {reason}")
    print(f"[RMS] Server : {url}")
    print(f"[RMS] To track results, make sure the RMS server is running:")
    print(f"[RMS]   cd <path-to-RMS> && python backend/main.py")
    print(f"[RMS] Then create the regression in the GUI before running tests.")
    print(f"")


DEFAULT_URL = "http://localhost:8000"


def resolve_url(url=None):
    """RMS base URL: explicit argument, else $RMS_URL, else localhost."""
    return url or os.environ.get("RMS_URL") or DEFAULT_URL


def derive_status(total, passed, failed, timeout=0):
    """Status for a finished run, matching the server's own derivation.

    Sent explicitly rather than left to the server so that `timeout` — which the
    server cannot distinguish from a plain failure once the counts are merged —
    survives to the dashboard. A run that produced no results at all is
    `aborted`: the tests did not fail, they never reported.
    """
    if total <= 0:
        return "aborted"
    if failed:
        return "failed"
    if timeout:
        return "timeout"
    return "passed"


def push(regression_id, total, passed, failed, timeout=0,
         url=None, start=None, end=None, log=None, machine_log=None,
         status=None):
    """Push one result row to the RMS. Returns an exit code; never raises.

    Callable directly so run_report can push in-process, with no shell command
    to quote and no interpreter path to get wrong on the way.
    """
    url = resolve_url(url)

    if not _HAS_REQUESTS:
        _warn_rms_unavailable(url, "'requests' package not installed (pip install requests)")
        return 0

    if passed + failed + timeout > total:
        print(f"[RMS] ERROR: passed ({passed}) + failed ({failed}) + timeout ({timeout}) "
              f"exceeds total ({total})")
        return 1

    if not regression_id:
        print("[RMS] ERROR: no regression id given; nothing to push to.")
        return 1

    if status and status not in STATUSES:
        print(f"[RMS] ERROR: invalid status '{status}'; expected one of {', '.join(STATUSES)}")
        return 1

    payload = {
        "id":            regression_id,
        "total_tests":   total,
        "passed_tests":  passed,
        "failed_tests":  failed,
        "timeout_tests": timeout,
    }
    if status:      payload["status"]           = status
    if start:       payload["start_time"]       = start
    if end:         payload["end_time"]         = end
    if log:         payload["log_path"]         = log
    if machine_log: payload["machine_log_path"] = machine_log

    return _post(payload, url, regression_id)


def main():
    args = parse_args()
    return push(args.id, args.total, args.passed, args.failed,
                timeout=args.timeout, url=args.url, start=args.start,
                end=args.end, log=args.log, machine_log=args.machine_log,
                status=args.status)


def _post(payload, url, regression_id):
    """POST the payload and report the outcome. Returns an exit code.

    Every failure path returns 0: a reporting problem must never fail the
    regression that produced the results.
    """
    # ASCII only: this runs on a Windows cp1252 console where a Unicode arrow
    # raises UnicodeEncodeError and would abort the regression flow.
    print(f"[RMS] Pushing result for '{regression_id}' -> {url} ...")

    try:
        r = requests.post(f"{url}/api/runs/result", json=payload, timeout=10)

    except requests.ConnectionError:
        _warn_rms_unavailable(url, "Connection refused - server is not running")
        return 0

    except requests.Timeout:
        _warn_rms_unavailable(url, "Request timed out after 10 seconds")
        return 0

    except Exception as e:
        _warn_rms_unavailable(url, str(e))
        return 0

    if r.status_code == 201:
        data = r.json()
        rate = round(data["passed_tests"] / data["total_tests"] * 100, 1) if data["total_tests"] else 0
        print(f"[RMS] Pushed successfully:")
        print(f"[RMS]   Regression : {data['regression_id']}")
        print(f"[RMS]   Status     : {data['status']}")
        print(f"[RMS]   Total      : {data['total_tests']}")
        print(f"[RMS]   Passed     : {data['passed_tests']}")
        print(f"[RMS]   Failed     : {data['failed_tests']}")
        # .get: an RMS predating timeout_tests echoes a row without the field,
        # and a KeyError here would be reported as a failed push of a row the
        # server actually accepted.
        print(f"[RMS]   Timeout    : {data.get('timeout_tests', payload['timeout_tests'])}")
        print(f"[RMS]   Pass rate  : {rate}%")
        print(f"[RMS]   Executed at: {data['executed_at']}")
        return 0

    elif r.status_code == 404:
        detail = r.json().get('detail', r.text) if r.headers.get('content-type', '').startswith('application/json') else r.text
        _warn_rms_unavailable(
            url,
            f"Regression '{regression_id}' not found in RMS (404). Create it in the GUI first."
        )
        print(f"[RMS] Server said: {detail}")
        return 0

    elif r.status_code in (401, 403):
        # This script deliberately sends no X-API-Key header. An RMS with auth
        # switched on will reject every push, and a bare "Unexpected response
        # 403" gives no clue why results stopped appearing in the dashboard.
        detail = (r.json().get('detail', r.text)
                  if r.headers.get('content-type', '').startswith('application/json')
                  else r.text)
        print(f"")
        print(f"[RMS] WARNING: Result was NOT pushed - rejected by the server ({r.status_code}).")
        print(f"[RMS] Server : {url}")
        print(f"[RMS] Reason : {detail}")
        print(f"[RMS] This RMS requires an API key. push_result.py does not send one,")
        print(f"[RMS] so every push will be rejected until either:")
        print(f"[RMS]   - authentication is turned off on the RMS backend, or")
        print(f"[RMS]   - API key support is re-enabled in push_result.py")
        print(f"[RMS] The regression itself is unaffected.")
        print(f"")
        return 0

    else:
        print(f"[RMS] Unexpected response {r.status_code}: {r.text}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
