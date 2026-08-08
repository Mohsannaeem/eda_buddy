"""
push_result.py — Push a regression result to the RMS (Result Management System).

The regression ID must already exist in the RMS GUI before you can push results
to it. This script only adds a new run row — it never creates a new regression.

Usage:
  python push_result.py --id MY_REGRESSION --total 46 --passed 44 --failed 2

Optional flags:
  --url    RMS backend base URL  (default: http://localhost:8000)
  --start  Start time ISO string (default: server sets to now)
  --end    End time ISO string   (default: server sets to now)
  --log    Path to regression log file

If the RMS server is unreachable the script exits cleanly with a warning so
the regression Makefile flow is NOT interrupted.
"""

import argparse
import sys

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


def parse_args():
    p = argparse.ArgumentParser(description="Push a result entry to the RMS.")
    p.add_argument("--id",     required=True,  help="Regression ID (must exist in the RMS GUI)")
    p.add_argument("--total",  required=True,  type=int, help="Total number of tests")
    p.add_argument("--passed", required=True,  type=int, help="Number of tests that passed")
    p.add_argument("--failed", required=True,  type=int, help="Number of tests that failed")
    p.add_argument("--url",    default="http://localhost:8000", help="RMS backend base URL")
    p.add_argument("--start",  default=None,   help="Start time (ISO 8601)")
    p.add_argument("--end",    default=None,   help="End time (ISO 8601)")
    p.add_argument("--log",    default=None,   help="Path to the regression log file")
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


def push(regression_id, total, passed, failed,
         url=DEFAULT_URL, start=None, end=None, log=None):
    """Push one result to the RMS. Returns an exit code; never raises.

    Callable directly so run_report can push in-process, with no shell command
    to quote and no interpreter path to get wrong on the way.
    """
    url = url or DEFAULT_URL

    if not _HAS_REQUESTS:
        _warn_rms_unavailable(url, "'requests' package not installed (pip install requests)")
        return 0

    if passed + failed > total:
        print(f"[RMS] ERROR: passed ({passed}) + failed ({failed}) exceeds total ({total})")
        return 1

    if not regression_id:
        print("[RMS] ERROR: no regression id given; nothing to push to.")
        return 1

    payload = {
        "id":           regression_id,
        "total_tests":  total,
        "passed_tests": passed,
        "failed_tests": failed,
    }
    if start: payload["start_time"] = start
    if end:   payload["end_time"]   = end
    if log:   payload["log_path"]   = log

    return _post(payload, url, regression_id)


def main():
    args = parse_args()
    return push(args.id, args.total, args.passed, args.failed,
                url=args.url, start=args.start, end=args.end, log=args.log)


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
