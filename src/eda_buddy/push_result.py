"""
push_result.py — Push a regression result to the RMS (Result Management System).

The regression ID must already exist in the RMS GUI before you can push results
to it. This script only writes a run_results row — it never creates a new
regression.

A run occupies ONE row from first update to finish. `--intermediate` sends
final=false, which keeps that row `running` with no end_time, so the next push
updates it in place instead of inserting another. The closing push (no flag)
settles the terminal status, stamps end_time, and is the only one that sends the
result email.

Usage:
  # at job start, before the first test — opens the regression as running
  python -m eda_buddy.push_result --id MY_REGRESSION --mark-running

  # during the run, as often as there is progress
  python -m eda_buddy.push_result --id MY_REGRESSION --total 46 --passed 12 \
                                  --failed 1 --intermediate

  # once, to close the run out and send the mail
  python -m eda_buddy.push_result --id MY_REGRESSION --total 46 --passed 44 \
                                  --failed 1 --timeout 1

Optional flags:
  --timeout Tests that hit their time limit (default: 0)
  --mark-running  Job-start signal: PATCH /api/runs/<id> to status=running,
                  progress=0. Takes no counts. Records --pipeline-url (or
                  $BUILD_URL) so the dashboard links back to the CI job.
  --intermediate  Mid-run update: reuse the open running row, no email
  --status  Explicit run status: passed|failed|build_fail|timeout|aborted
            (default: derived by the server from the counts). Use build_fail
            when the build did not compile and no tests ran. Honored only on
            the final push — an intermediate row stays `running`.
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
from urllib.parse import quote

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


STATUSES = ("passed", "failed", "build_fail", "timeout", "aborted")


def parse_args():
    p = argparse.ArgumentParser(description="Push a result entry to the RMS.")
    p.add_argument("--id",      required=True,  help="Regression ID (must exist in the RMS GUI)")
    # Not required=True: --mark-running carries no counts. Enforced below for
    # every other mode, so a result push still cannot be sent half-filled.
    p.add_argument("--total",   default=None, type=int, help="Total number of tests")
    p.add_argument("--passed",  default=None, type=int, help="Number of tests that passed")
    p.add_argument("--failed",  default=None, type=int, help="Number of tests that failed")
    p.add_argument("--mark-running", dest="mark_running", action="store_true",
                   help="Open the regression at job start (PATCH /api/runs/<id>) "
                        "instead of pushing a result row")
    p.add_argument("--pipeline-url", dest="pipeline_url", default=None,
                   help="CI job URL recorded with --mark-running (default: $BUILD_URL)")
    p.add_argument("--timeout", default=0, type=int, help="Tests that hit their time limit")
    p.add_argument("--intermediate", dest="final", action="store_false", default=True,
                   help="Mid-run update: keep the run's row open, send no email")
    p.add_argument("--status",  default=None, choices=list(STATUSES),
                   help="Explicit run status (default: derived from counts). "
                        "Honored only on the final push")
    p.add_argument("--url",     default=None, help="RMS backend base URL")
    p.add_argument("--start",   default=None,   help="Start time (ISO 8601)")
    p.add_argument("--end",     default=None,   help="End time (ISO 8601). Ignored on an "
                                                     "intermediate push, which leaves the row open")
    p.add_argument("--log",     default=None,   help="Path to the regression log file")
    p.add_argument("--machine-log", dest="machine_log", default=None,
                   help="On-agent logs path (email 'Logs path' row only)")
    a = p.parse_args()

    if not a.mark_running:
        missing = [n for n in ("total", "passed", "failed") if getattr(a, n) is None]
        if missing:
            p.error("the following arguments are required: "
                    + ", ".join("--" + n for n in missing))
    return a


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


def mark_running(regression_id, url=None, pipeline_url=None, progress=0):
    """Open a regression at the start of a job. Returns an exit code; never raises.

    PATCHes the regression itself (`/api/runs/<id>`), not a run_results row —
    this is the "a job has started" signal, so the dashboard shows the run as
    live from the moment the first test is launched rather than from whenever
    the first result lands. The result rows are a separate stream (`push`).

    pipeline_url defaults to $BUILD_URL, which Jenkins sets on every build, so
    the generated Makefile needs no plumbing to pass it through.
    """
    url = resolve_url(url)

    if not _HAS_REQUESTS:
        _warn_rms_unavailable(url, "'requests' package not installed (pip install requests)")
        return 0

    if not regression_id:
        print("[RMS] ERROR: no regression id given; nothing to mark running.")
        return 1

    payload = {"status": "running", "progress": progress}
    pipeline_url = pipeline_url or os.environ.get("BUILD_URL")
    if pipeline_url:
        payload["pipeline_url"] = pipeline_url

    # Escaped: an id is free text in the GUI, and a '/' or '#' in it would
    # otherwise silently address a different endpoint.
    endpoint = f"{url}/api/runs/{quote(str(regression_id), safe='')}"
    print(f"[RMS] Marking '{regression_id}' running -> {endpoint} ...")

    try:
        r = requests.patch(endpoint, json=payload, timeout=10)
    except requests.ConnectionError:
        _warn_mark_skipped(url, "Connection refused - server is not running")
        return 0
    except requests.Timeout:
        _warn_mark_skipped(url, "Request timed out after 10 seconds")
        return 0
    except Exception as e:
        _warn_mark_skipped(url, str(e))
        return 0

    if r.status_code in (200, 201, 204):
        suffix = f" (pipeline: {pipeline_url})" if pipeline_url else ""
        print(f"[RMS] Marked running{suffix}")
        return 0

    detail = r.text
    if r.headers.get('content-type', '').startswith('application/json'):
        try:
            detail = r.json().get('detail', r.text)
        except ValueError:
            pass
    _warn_mark_skipped(url, f"{r.status_code}: {detail}")
    return 0


def _warn_mark_skipped(url, reason):
    """A failed mark-running is a missing status, not a failed regression.

    Deliberately quieter than _warn_rms_unavailable: this runs before the tests,
    and a wall of setup advice at the top of every regression log would train
    people to ignore the section where the real result push reports problems.
    """
    print(f"[RMS] mark-running skipped (continuing): {reason}")
    print(f"[RMS] Server : {url}")


def derive_status(total, passed, failed, timeout=0, build_fail=0):
    """Status for a finished run, matching the server's own derivation.

    Sent explicitly rather than left to the server so that `timeout` — which the
    server cannot distinguish from a plain failure once the counts are merged —
    survives to the dashboard. A run that produced no results at all is
    `aborted`: the tests did not fail, they never reported.

    `build_fail` counts tests whose design never elaborated. When that is the
    whole run, the run is `build_fail`: nothing was compiled, so no test result
    exists to report. When only some tests failed to build, the run is `failed`
    — a per-test build failure is one failure among results, and the RMS row
    carries a single status for the whole run.
    """
    if total <= 0:
        return "aborted"
    if build_fail and not (passed or failed or timeout):
        return "build_fail"
    if failed or build_fail:
        return "failed"
    if timeout:
        return "timeout"
    return "passed"


def push(regression_id, total, passed, failed, timeout=0,
         url=None, start=None, end=None, log=None, machine_log=None,
         status=None, final=True):
    """Push one result row to the RMS. Returns an exit code; never raises.

    Callable directly so run_report can push in-process, with no shell command
    to quote and no interpreter path to get wrong on the way.

    final=False marks a mid-run update: the server holds the row open at
    `running` and the next push updates it rather than inserting a second one,
    and no result email goes out until the run is closed with final=True.
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
    # Always sent, both values. Relying on the server's `final: bool = True`
    # default would make a closing push indistinguishable on the wire from a
    # caller that predates the field — and if that default ever flips, every
    # run silently stops settling: no terminal status, no end_time, no email,
    # and no error anywhere to notice it by.
    payload["final"] = bool(final)
    if status:      payload["status"]           = status
    if start:       payload["start_time"]       = start
    # end_time only on the closing push: it is the row's finish line, and a row
    # that carries one is a run that stopped. Sending it mid-run would settle a
    # row the next push still has to update.
    if end and final:
        payload["end_time"] = end
    if log:         payload["log_path"]         = log
    if machine_log: payload["machine_log_path"] = machine_log

    return _post(payload, url, regression_id, final=final)


def main():
    args = parse_args()
    if args.mark_running:
        return mark_running(args.id, url=args.url, pipeline_url=args.pipeline_url)
    return push(args.id, args.total, args.passed, args.failed,
                timeout=args.timeout, url=args.url, start=args.start,
                end=args.end, log=args.log, machine_log=args.machine_log,
                status=args.status, final=args.final)


def _report_row(response, payload, final):
    """Print the row the server recorded. Never raises.

    Every field is read through .get with what we sent as the fallback. Two
    reasons, both real since `final` landed: an intermediate push echoes a row
    that is deliberately unsettled — `end_time` is null and `executed_at` need
    not exist yet — and an RMS predating a field omits it entirely. A KeyError
    here would surface as a crashed reporting step for a push the server
    accepted, and in the per-test hook that means one per test.
    """
    try:
        data = response.json()
        if not isinstance(data, dict):
            data = {}
    except ValueError:
        data = {}

    def field(name, default='-'):
        value = data.get(name, payload.get(name, default))
        return default if value is None else value

    total  = field('total_tests',  payload['total_tests'])
    passed = field('passed_tests', payload['passed_tests'])
    rate   = round(passed / total * 100, 1) if isinstance(total, int) and total else 0

    print(f"[RMS] {'Pushed successfully' if final else 'Progress updated'}:")
    print(f"[RMS]   Regression : {data.get('regression_id', payload['id'])}")
    print(f"[RMS]   Status     : {field('status', 'running' if not final else '-')}")
    print(f"[RMS]   Total      : {total}")
    print(f"[RMS]   Passed     : {passed}")
    print(f"[RMS]   Failed     : {field('failed_tests', payload['failed_tests'])}")
    print(f"[RMS]   Timeout    : {field('timeout_tests', payload['timeout_tests'])}")
    print(f"[RMS]   Pass rate  : {rate}%")
    print(f"[RMS]   Executed at: {field('executed_at')}")
    # Echoed back so the log shows which push settled the run. A regression that
    # never sent final=true leaves its row open forever, and without this line
    # the only symptom is a dashboard entry stuck at `running` with no clue why.
    print(f"[RMS]   Final      : {field('final', payload['final'])}")


def _post(payload, url, regression_id, final=True):
    """POST the payload and report the outcome. Returns an exit code.

    Every failure path returns 0: a reporting problem must never fail the
    regression that produced the results.
    """
    # ASCII only: this runs on a Windows cp1252 console where a Unicode arrow
    # raises UnicodeEncodeError and would abort the regression flow.
    kind = "result" if final else "progress"
    print(f"[RMS] Pushing {kind} for '{regression_id}' -> {url} ...")

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

    # 200 as well as 201: an intermediate push updates the run's open row rather
    # than inserting one, and an update is not a creation. Treating only 201 as
    # success would report every mid-run update as "Unexpected response 200"
    # while the server was in fact recording them.
    if r.status_code in (200, 201):
        _report_row(r, payload, final)
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
