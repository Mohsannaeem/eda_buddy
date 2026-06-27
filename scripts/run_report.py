"""
run_report.py — EDA Buddy Test Run Report Generator

Walks <root>/<component>/run/<test>/ directories, parses the latest sim.log
per test, and prints a pass/fail report with failure signatures.
"""
import os
import re
from datetime import datetime

# ── ANSI colours ─────────────────────────────────────────────────────────────

_C = {
    'green':  "\033[32m",
    'red':    "\033[31m",
    'yellow': "\033[33m",
    'cyan':   "\033[36m",
    'bold':   "\033[1m",
    'reset':  "\033[0m",
}

def _c(text, *codes, use_color=True):
    if not use_color:
        return text
    prefix = "".join(_C[c] for c in codes)
    return f"{prefix}{text}{_C['reset']}"

# ── Log parser ────────────────────────────────────────────────────────────────

_SEED_RE    = re.compile(r'#\s+Sv_Seed\s*=\s*(\d+)')
_START_RE   = re.compile(r'#\s+Start time:\s*(.+)')
_ELAPSED_RE = re.compile(r'Elapsed time:\s*([\d:]+)')
_SIMTIME_RE = re.compile(r'#\s+Time:\s*([\d]+ \w+)\s+Iteration:')
_ERR_CNT_RE = re.compile(r'#\s+UVM_ERROR\s*:\s*(\d+)')
_FAT_CNT_RE = re.compile(r'#\s+UVM_FATAL\s*:\s*(\d+)')
_WRN_CNT_RE = re.compile(r'#\s+UVM_WARNING\s*:\s*(\d+)')
_QUITCNT_RE = re.compile(r'#\s+Quit count\s*:\s*(\d+)')
_FINISH_RE  = re.compile(r'\$finish')

# Actual UVM_ERROR/FATAL message lines (not the count summary "UVM_ERROR :  0")
_ERRMSG_RE  = re.compile(r'#\s+(UVM_(?:ERROR|FATAL)\s+\S.*?@\s*\d+.*)')

def parse_sim_log(log_path):
    """
    Returns:
        status      : 'PASS' | 'FAIL' | 'TIMEOUT' | 'NO_LOG'
        seed        : actual seed string
        start_time  : wall-clock start from log header
        elapsed     : wall-clock elapsed e.g. '0:00:18'
        sim_end_time: sim timestamp at $finish e.g. '1055 ns'
        uvm_errors  : int
        uvm_fatals  : int
        uvm_warnings: int
        quit_count  : int
        signatures  : list[str] — UVM_ERROR/FATAL message lines for failures
        log_path    : str
    """
    r = dict(status='NO_LOG', seed='-', start_time='-', elapsed='-',
             sim_end_time='-', uvm_errors=0, uvm_fatals=0, uvm_warnings=0,
             quit_count=0, signatures=[], log_path=log_path)

    if not os.path.isfile(log_path):
        return r

    try:
        with open(log_path, errors='ignore') as f:
            lines = f.readlines()
        content = ''.join(lines)
    except Exception:
        return r

    def first(pat):
        m = pat.search(content)
        return m.group(1).strip() if m else '-'

    r['seed']       = first(_SEED_RE)
    r['start_time'] = first(_START_RE)
    r['elapsed']    = first(_ELAPSED_RE)

    # Sim end time: last match of "Time: X ns  Iteration:" (near $finish)
    for line in reversed(lines):
        m = _SIMTIME_RE.search(line)
        if m:
            r['sim_end_time'] = m.group(1)
            break

    m = _ERR_CNT_RE.search(content)
    if m: r['uvm_errors']   = int(m.group(1))
    m = _FAT_CNT_RE.search(content)
    if m: r['uvm_fatals']   = int(m.group(1))
    m = _WRN_CNT_RE.search(content)
    if m: r['uvm_warnings'] = int(m.group(1))
    m = _QUITCNT_RE.search(content)
    if m: r['quit_count']   = int(m.group(1))

    # Failure signatures: actual UVM_ERROR/FATAL message lines
    for line in lines:
        m = _ERRMSG_RE.match(line)
        if m:
            sig = m.group(1).strip()
            # Skip the count summary lines ("UVM_ERROR :  0")
            if re.match(r'UVM_(?:ERROR|FATAL)\s*:', sig):
                continue
            r['signatures'].append(sig)

    # Status determination
    has_summary = '--- UVM Report Summary ---' in content
    has_finish  = bool(_FINISH_RE.search(content))

    if not has_finish and not has_summary:
        r['status'] = 'TIMEOUT'
    elif r['uvm_fatals'] > 0 or r['uvm_errors'] > 0:
        r['status'] = 'FAIL'
    else:
        r['status'] = 'PASS'

    return r

# ── Directory walker ──────────────────────────────────────────────────────────

def latest_log(test_dir):
    """
    Return path to sim.log for this test dir, or None.

    Two layouts are supported:
    - Regression mode:  <test_dir>/sim.log           (flat, inside session dir)
    - Standalone mode:  <test_dir>/run_<ts>/sim.log  (timestamped subdir)
    """
    # Regression layout: direct sim.log
    direct = os.path.join(test_dir, 'sim.log')
    if os.path.exists(direct):
        return direct
    # Standalone layout: most recent run_* subdir
    try:
        runs = sorted(
            d for d in os.listdir(test_dir)
            if d.startswith('run_') and os.path.isdir(os.path.join(test_dir, d))
        )
    except FileNotFoundError:
        return None
    if not runs:
        return None
    return os.path.join(test_dir, runs[-1], 'sim.log')

def collect_results(root, component=None, tests=None, reg_dir=None):
    """
    Walk run directories and return:
        { comp_name: [ {test, log_path, **parse_result}, ... ] }

    reg_dir: when set (regression group run), scan that session dir directly
             instead of <root>/<comp>/run/ — test subdirs sit flat inside it.
    tests:   optional ordered list of test names to include (omit = all found).
    """
    report = {}
    comp_dirs = (
        [(component, os.path.join(root, component))]
        if component
        else [(d, os.path.join(root, d)) for d in sorted(os.listdir(root))
              if os.path.isdir(os.path.join(root, d))]
    )

    for comp_name, comp_path in comp_dirs:
        # reg_dir overrides the normal run root for this component
        run_root = reg_dir if reg_dir else os.path.join(comp_path, 'run')
        if not os.path.isdir(run_root):
            continue

        if tests:
            names = list(tests)
        else:
            names = sorted(d for d in os.listdir(run_root)
                           if os.path.isdir(os.path.join(run_root, d)))

        entries = []
        for test_name in names:
            test_dir = os.path.join(run_root, test_name)
            log = latest_log(test_dir) if os.path.isdir(test_dir) else None
            parsed = parse_sim_log(log) if log else dict(
                status='NO_LOG', seed='-', start_time='-', elapsed='-',
                sim_end_time='-', uvm_errors=0, uvm_fatals=0, uvm_warnings=0,
                quit_count=0, signatures=[], log_path='-')
            entries.append({'test': test_name, **parsed})
        if entries:
            report[comp_name] = entries
    return report

# ── Table formatter ───────────────────────────────────────────────────────────
#
# Each test occupies 2+ rows inside a box table:
#   Row 1 : | # | Test name | Status | Seed | Elapsed | SimTime |
#   Row 2 : |   | log: <path spanning all remaining columns>      |
#   Row 3+: |   | signature lines (FAIL/TIMEOUT only)             |
# A +---+---+ separator is drawn between every test entry.

_COLS = [
    ('#',        4,  'r'),
    ('Test',    50,  'l'),
    ('Status',   9,  'c'),
    ('Seed',    12,  'r'),
    ('Elapsed',  8,  'r'),
    ('SimTime', 10,  'r'),
]
_CW = [c[1] for c in _COLS]                   # content widths
_CA = [c[2] for c in _COLS]                   # alignments
_TW = 1 + sum(w + 3 for w in _CW)             # total table width incl. all borders
_SW = _TW - _CW[0] - 7                        # span-row text width (cols 2..end merged)

_STATUS_COLOR = {
    'PASS':    ('green',  'bold'),
    'FAIL':    ('red',    'bold'),
    'TIMEOUT': ('yellow', 'bold'),
    'NO_LOG':  ('yellow',),
}

def _tbl_sep():
    return '+' + '+'.join('-' * (w + 2) for w in _CW) + '+'

def _tbl_header(use_color=True):
    cells = [f' {h:^{w}} ' for h, w in zip([c[0] for c in _COLS], _CW)]
    return _c('|' + '|'.join(cells) + '|', 'bold', use_color=use_color)

def _tbl_row(idx, test_name, status, seed, elapsed, simtime, use_color=True):
    vals = [str(idx), str(test_name), str(status), str(seed), str(elapsed), str(simtime)]
    cells = []
    for i, (v, w, a) in enumerate(zip(vals, _CW, _CA)):
        padded = f'{v:>{w}}' if a == 'r' else f'{v:^{w}}' if a == 'c' else f'{v:<{w}}'
        if i == 2 and status in _STATUS_COLOR and use_color:
            padded = _c(padded, *_STATUS_COLOR[status], use_color=use_color)
        cells.append(f' {padded} ')
    return '|' + '|'.join(cells) + '|'

def _tbl_span(text, color=None, use_color=True):
    """A row where cols 2..end are merged — used for log path and signatures."""
    if len(text) > _SW:
        text = '...' + text[-((_SW - 3)):]   # keep the tail (filename visible)
    padded = f'{text:<{_SW}}'
    if color and use_color:
        padded = _c(padded, color, use_color=use_color)
    return f'| {"":>{_CW[0]}} | {padded} |'

def generate_report(root, project_name='', component=None, use_color=True, tests=None, reg_dir=None):
    """
    Generate the full report string (console version).
    Returns (report_lines: list[str], plain_lines: list[str])
    where plain_lines strip ANSI for file output.
    """
    results = collect_results(root, component, tests=tests, reg_dir=reg_dir)
    if not results:
        msg = f"No test run directories found under: {root}"
        return [msg], [msg]

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    header_title = "EDA BUDDY - TEST RUN REPORT"

    lines = []  # coloured
    plain = []  # plain

    def emit(line=''):
        lines.append(line)
        # strip ANSI for plain
        plain.append(re.sub(r'\033\[[0-9;]*m', '', line))

    emit('=' * _TW)
    emit(_c(f"  {header_title}", 'bold', 'cyan', use_color=use_color))
    if project_name:
        emit(f"  Project   : {project_name}")
    emit(f"  Generated : {now}")
    emit(f"  Root      : {root}")
    emit('=' * _TW)

    overall_total = overall_pass = overall_fail = overall_timeout = overall_nolog = 0

    for comp, entries in results.items():
        emit()
        emit(_c(f"  Component: {comp}", 'bold', 'cyan', use_color=use_color))
        emit(_tbl_sep())
        emit(_tbl_header(use_color=use_color))
        emit(_tbl_sep())

        comp_pass = comp_fail = comp_timeout = comp_nolog = 0

        for idx, e in enumerate(entries, 1):
            st = e['status']

            # Row 1: main stats
            emit(_tbl_row(idx, e['test'], st,
                          e['seed'], e['elapsed'], e['sim_end_time'],
                          use_color=use_color))

            # Row 2: log path
            log_text = f"log: {e['log_path']}"
            log_color = 'red' if st in ('FAIL', 'TIMEOUT') else None
            emit(_tbl_span(log_text, color=log_color, use_color=use_color))

            # Row 3+: failure detail
            if st == 'TIMEOUT':
                emit(_tbl_span('TIMEOUT - simulation did not reach $finish (check UVM_TIMEOUT)',
                               color='yellow', use_color=use_color))
            elif st == 'FAIL':
                for sig in e['signatures'][:5]:
                    emit(_tbl_span(sig, color='red', use_color=use_color))
                if len(e['signatures']) > 5:
                    emit(_tbl_span(f'... ({len(e["signatures"]) - 5} more errors)',
                                   color='red', use_color=use_color))
                elif not e['signatures']:
                    emit(_tbl_span('Errors/Fatals in UVM Report Summary',
                                   color='red', use_color=use_color))

            emit(_tbl_sep())

            if   st == 'PASS':    comp_pass    += 1
            elif st == 'FAIL':    comp_fail    += 1
            elif st == 'TIMEOUT': comp_timeout += 1
            else:                 comp_nolog   += 1

        total = len(entries)
        pct   = f"{100 * comp_pass / total:.1f}%" if total else "-%"
        summary = (f"  Summary  {comp}  |  "
                   f"Total: {total}   "
                   f"PASS: {comp_pass}   "
                   f"FAIL: {comp_fail}   "
                   f"TIMEOUT: {comp_timeout}   "
                   f"NO_LOG: {comp_nolog}   "
                   f"Pass rate: {pct}")
        emit(_c(summary, 'bold', use_color=use_color))
        emit()

        overall_total   += total
        overall_pass    += comp_pass
        overall_fail    += comp_fail
        overall_timeout += comp_timeout
        overall_nolog   += comp_nolog

    # Overall footer
    emit('=' * _TW)
    o_pct = f"{100 * overall_pass / overall_total:.1f}%" if overall_total else "-%"
    verdict = 'PASS' if overall_fail == 0 and overall_timeout == 0 else 'FAIL'
    emit(_c(f"  VERDICT : {verdict}", 'green' if verdict == 'PASS' else 'red', 'bold', use_color=use_color))
    emit(f"  Total: {overall_total}   "
         f"PASS: {overall_pass}   "
         f"FAIL: {overall_fail}   "
         f"TIMEOUT: {overall_timeout}   "
         f"NO_LOG: {overall_nolog}   "
         f"Pass rate: {o_pct}")
    emit('=' * _TW)

    return lines, plain

# ── Single-test one-liner (printed after each individual vsim call) ───────────

def _scan_run_dir(comp_run_dir):
    """Scan a directory and return (passed, failed, timeout) counts.

    Works for both layouts:
      Regression (REGDIR): <comp_run_dir>/<test>/sim.log
      Standalone:          <comp_run_dir>/<test>/run_<ts>/sim.log
    """
    passed = failed = timeout = 0
    if not os.path.isdir(comp_run_dir):
        return passed, failed, timeout
    for test_dir in os.listdir(comp_run_dir):
        test_path = os.path.join(comp_run_dir, test_dir)
        if not os.path.isdir(test_path):
            continue
        log = latest_log(test_path)
        if not log:
            continue
        st = parse_sim_log(log)['status']
        if st == 'PASS':
            passed += 1
        elif st == 'FAIL':
            failed += 1
        elif st == 'TIMEOUT':
            timeout += 1
    return passed, failed, timeout


def print_single_result(log_path, test_name, total=None, comp_run_dir=None):
    """
    Print a per-test status line after vsim finishes.

    When comp_run_dir is given (regression mode — REGDIR is set), also prints
    running totals scoped to that session directory only:

      [ PASS ]  tc_010  seed=738335390  sim=3355 ns  wall=0:00:17  log: .../sim.log
                Total=46 | Pass=10 | Fail=1 | Running=35

    In standalone mode (no comp_run_dir) only the status line is printed.
    """
    import sys
    use_color = hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()
    r  = parse_sim_log(log_path)
    st = r['status']

    tag = {
        'PASS':    _c('[ PASS ]', 'green',  'bold', use_color=use_color),
        'FAIL':    _c('[ FAIL ]', 'red',    'bold', use_color=use_color),
        'TIMEOUT': _c('[TIMEOUT]', 'yellow', 'bold', use_color=use_color),
        'NO_LOG':  _c('[NO LOG ]', 'yellow',         use_color=use_color),
    }.get(st, f'[{st:^7}]')

    print()
    status_line = (f"{tag}  {test_name:<52}"
                   f"  seed={r['seed']:<14}"
                   f"  sim={r['sim_end_time']:<12}"
                   f"  wall={r['elapsed']}"
                   f"  log: {log_path}")
    if st in ('FAIL', 'TIMEOUT'):
        print(_c(status_line, 'red', use_color=use_color))
    else:
        print(status_line)

    # Failure signatures
    if st == 'TIMEOUT':
        print(_c('          No $finish reached — simulation hit UVM_TIMEOUT', 'yellow', use_color=use_color))
    elif st == 'FAIL':
        for sig in r['signatures'][:3]:
            print(_c(f'          {sig}', 'red', use_color=use_color))
        if len(r['signatures']) > 3:
            print(_c(f'          ... ({len(r["signatures"]) - 3} more)', 'red', use_color=use_color))

    # Running totals — regression mode only, scoped to REGDIR (current session)
    if comp_run_dir and comp_run_dir.strip():
        passed, failed, timeout = _scan_run_dir(comp_run_dir)
        completed = passed + failed + timeout
        running   = max((total or 0) - completed, 0)
        total_str = str(total) if total is not None else '?'
        print(_c(f'          Total={total_str} | Pass={passed} | Fail={failed + timeout} | Pending={running}',
                 'cyan', use_color=use_color))
    print()

# ── Full regression report ────────────────────────────────────────────────────

def run_report(root, project_name='', component=None, save_to=None,
               tests=None, reg_dir=None, post_cmd=None):
    """Print the full report to stdout; optionally save plain text to save_to.

    post_cmd: shell command template with {total}, {passed}, {failed} placeholders.
              Executed after the report is printed with actual regression numbers.
    """
    import sys, subprocess
    use_color = hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()
    coloured, plain = generate_report(root, project_name, component,
                                      use_color=use_color, tests=tests, reg_dir=reg_dir)
    print('\n'.join(coloured))

    if save_to:
        os.makedirs(os.path.dirname(os.path.abspath(save_to)), exist_ok=True)
        with open(save_to, 'w') as f:
            f.write('\n'.join(plain))
        print(f"[REPORT] Saved: {save_to}")

    if post_cmd and post_cmd.strip():
        # Tally actuals from the scan so placeholders get real numbers
        passed, failed, timeout = _scan_run_dir(reg_dir or os.path.join(root, component or '', 'run'))
        total  = passed + failed + timeout
        cmd    = post_cmd.format(total=total, passed=passed, failed=failed + timeout)
        print(f"[POST-CMD] {cmd}")
        subprocess.run(cmd, shell=True, check=False)

# ── CLI entry point (invoked from Makefile) ───────────────────────────────────

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='EDA Buddy Test Run Report')

    # Mode A — single test status line (called after each vsim)
    p.add_argument('--single-log',   default=None, help='Path to one sim.log — print one-line result')
    p.add_argument('--test-name',    default='',   help='Test name to display in single-log mode')
    p.add_argument('--total',        type=int, default=None, help='Total tests in this regression group')
    p.add_argument('--comp-run-dir', default=None, help='REGDIR to scan for session-scoped totals')

    # Mode B — full regression report (called after group target)
    p.add_argument('--root',         default=None, help='Project run root')
    p.add_argument('--component',    default=None, help='Limit report to one component')
    p.add_argument('--project-name', default='',   help='Display name for the project')
    p.add_argument('--save-to',      default=None, help='Write plain-text report to this file')
    p.add_argument('--tests',        default=None, help='Comma-separated test names to include (omit = all)')
    p.add_argument('--reg-dir',      default=None, help='Regression session dir to scan for logs')
    p.add_argument('--post-cmd',     default=None,
                   help='Shell command run after report; {total}/{passed}/{failed} substituted with actual counts')

    a = p.parse_args()

    if a.single_log:
        print_single_result(a.single_log,
                            a.test_name or os.path.basename(a.single_log),
                            total=a.total,
                            comp_run_dir=a.comp_run_dir)
    elif a.root:
        tests   = a.tests.split(',') if a.tests else None
        reg_dir = a.reg_dir or None
        run_report(a.root, a.project_name, a.component, a.save_to,
                   tests=tests, reg_dir=reg_dir, post_cmd=a.post_cmd)
    else:
        p.error('Provide either --single-log <path> or --root <path>')
