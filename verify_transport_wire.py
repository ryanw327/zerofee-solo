"""Run synthetic transport tests with only ephemeral IPv4 loopback sockets allowed."""
import argparse
import io
import json
from pathlib import Path
import sys
import unittest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json-report',type=Path)
    args=parser.parse_args();blocked=[]
    def guard(event,values):
        forbidden=event in ('subprocess.Popen','os.system') or (
            event=='ctypes.dlopen' and values and any(s in str(values[0]).lower() for s in ('nvcuda','nvrtc','cudart')))
        if event in ('socket.bind','socket.connect'):
            address=values[1] if len(values)>1 else None
            valid=isinstance(address,tuple) and len(address)==2 and address[0]=='127.0.0.1' and type(address[1]) is int
            if valid:
                valid=address[1]==0 if event=='socket.bind' else 1024<=address[1]<=65535 and address[1]!=8332
            forbidden=not valid
        if forbidden:blocked.append(event);raise RuntimeError('Transport verification forbids '+event)
    sys.addaudithook(guard)
    import test_rpc_wire, test_local_rpc
    stream=io.StringIO()
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromModule(module) for module in (test_rpc_wire,test_local_rpc)))
    report=dict(status='PASS' if result.wasSuccessful() and not blocked else 'FAIL',tests=result.testsRun,
        failures=len(result.failures),errors=len(result.errors),blocked_side_effect_attempts=blocked,
        network_scope='Ephemeral synthetic IPv4 loopback only',real_core_used=False,gpu_executed=False,
        test_modules=['test_rpc_wire','test_local_rpc'])
    if args.json_report:args.json_report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    if not result.wasSuccessful():print(stream.getvalue())
    print(json.dumps(report,indent=2))
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
