"""Offline-only checks. Real GPU, process launches and network activity are forbidden."""
import argparse
import ast
import contextlib
import gc
import importlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent
MODULES=['test_gpu_watchdog','test_builder','test_core_session','test_live','test_managed_core','test_managed_runner',
    'test_startup','test_startup_lifecycle','test_tip_race','test_performance','test_thermal_safety',
    'test_mining_sessions','test_gui','test_gui_window','test_win_moment','test_win_history',
    'test_optional_tip','test_winning_ui','test_advanced_tuning','test_tuning_benchmark',
    'test_risk_notice','test_release_defaults','test_audit_release','test_dispatch_safety','test_gpu_vectors','test_license_notices','test_rpc_envelope','test_spend_integrity','test_compact_benchmark','test_confirmation_snapshot','test_submission_result','test_gpu_identity']

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json-report',type=Path,help='Optional report path, outside the source tree recommended')
    args=parser.parse_args()
    blocked=[]
    def guard(event,values):
        if event in ('subprocess.Popen','os.system','socket.connect','socket.bind') or (
            event=='ctypes.dlopen' and values and any(s in str(values[0]).lower() for s in ('nvcuda','nvrtc','cudart'))):
            blocked.append(event);raise RuntimeError('Offline verification forbids '+event)
    sys.addaudithook(guard)
    for path in ROOT.glob('*.py'):ast.parse(path.read_text(encoding='utf-8'))
    modules=[importlib.import_module(name) for name in MODULES]
    for name,module in tuple(sys.modules.items()):
        if name in ('miner','miner_gui','gui_runner','tip_support','win_history','gpu_hasher','advanced_settings'):
            assert Path(module.__file__).resolve().parent==ROOT,'Module imported outside source tree'
    class Result(unittest.TextTestResult):
        def startTest(self,test):gc.collect();super().startTest(test)
    stream=io.StringIO()
    with patch('core_session.discover_session',side_effect=ValueError('Offline test only')),contextlib.redirect_stdout(io.StringIO()):
        result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Result).run(
            unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(module) for module in modules))
    report=dict(status='PASS' if result.wasSuccessful() and not blocked else 'FAIL',tests=result.testsRun,
        failures=len(result.failures),errors=len(result.errors),blocked_side_effect_attempts=blocked,
        real_gpu_executed=False,core_started=False,real_benchmark_executed=False,test_modules=MODULES)
    if not result.wasSuccessful():print(stream.getvalue())
    if args.json_report:args.json_report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))
    return 0 if report['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
