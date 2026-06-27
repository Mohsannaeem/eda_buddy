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


def main():
    args = parse_args()

    if not _HAS_REQUESTS:
        _warn_rms_unavailable(args.url, "'requests' package not installed (pip install requests)")
        sys.exit(0)

    if args.passed + args.failed > args.total:
        print(f"[RMS] ERROR: passed ({args.passed}) + failed ({args.failed}) exceeds total ({args.total})")
        sys.exit(1)

    payload = {
        "id":           args.id,
        "total_tests":  args.total,
        "passed_tests": args.passed,
        "failed_tests": args.failed,
    }
    if args.start: payload["start_time"] = args.start
    if args.end:   payload["end_time"]   = args.end
    if args.log:   payload["log_path"]   = args.log

    print(f"[RMS] Pushing result for '{args.id}' → {args.url} ...")

    try:
        r = requests.post(f"{args.url}/api/runs/result", json=payload, timeout=10)

    except requests.ConnectionError:
        _warn_rms_unavailable(args.url, "Connection refused — server is not running")
        sys.exit(0)

    except requests.Timeout:
        _warn_rms_unavailable(args.url, "Request timed out after 10 seconds")
        sys.exit(0)

    except Exception as e:
        _warn_rms_unavailable(args.url, str(e))
        sys.exit(0)

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

    elif r.status_code == 404:
        detail = r.json().get('detail', r.text) if r.headers.get('content-type', '').startswith('application/json') else r.text
        _warn_rms_unavailable(
            args.url,
            f"Regression '{args.id}' not found in RMS (404). Create it in the GUI first."
        )
        print(f"[RMS] Server said: {detail}")
        sys.exit(0)

    else:
        print(f"[RMS] Unexpected response {r.status_code}: {r.text}")
        sys.exit(0)


if __name__ == "__main__":
    main()
