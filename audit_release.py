"""Allowlist and pattern audit; prints rule IDs and file names, never matched secrets."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parent
PUBLIC_ADDRESS='bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4'
APPROVED_PUBLIC_TIP='bc1qu4xz46wfg0fap3hy27g0cjddvvqfrxfclwqdx4'
REGTEST_VECTOR='bcrt1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080'

def audit(root=ROOT):
    allowed=json.loads((root/'publication-allowlist.json').read_text(encoding='utf-8'))
    if type(allowed) is not list or len(allowed)!=len(set(allowed)):
        raise ValueError('Invalid publication allowlist')
    findings=[];hashes={}
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and '.git' not in p.relative_to(root).parts}
    for name in sorted(actual-set(allowed)):findings.append(dict(file=name,rule='unexpected-file'))
    for name in sorted(set(allowed)-actual):findings.append(dict(file=name,rule='missing-allowlisted-file'))
    patterns={
        'personal-profile-path':r'(?i)(?:[A-Z]:[\\/]+Users[\\/]+|/'+'Users'+r'/|/'+'home'+r'/)',
        'private-key-pem':r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        'github-token':r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b',
        'aws-access-key':r'\bAKIA[0-9A-Z]{16}\b',
        'bitcoin-private-key-shaped':r'\b(?:5[1-9A-HJ-NP-Za-km-z]{50}|[KL][1-9A-HJ-NP-Za-km-z]{51})\b',
    }
    for name in sorted(actual & set(allowed)):
        path=root/name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            findings.append(dict(file=name,rule='symlink-or-escape'));continue
        data=path.read_bytes()
        if name!='RELEASE_MANIFEST.json':hashes[name]=hashlib.sha256(data).hexdigest()
        if name=='pickaxe.ico':
            if data[:4]!=b'\x00\x00\x01\x00':findings.append(dict(file=name,rule='unexpected-icon-format'))
            continue
        try:text=data.decode('utf-8')
        except UnicodeDecodeError:
            findings.append(dict(file=name,rule='unexpected-binary'));continue
        for rule,pattern in patterns.items():
            if re.search(pattern,text):findings.append(dict(file=name,rule=rule))
        for address in re.findall(r'\b(?:bc1|bcrt1)[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{8,87}\b',text):
            if address not in (PUBLIC_ADDRESS,REGTEST_VECTOR) and not (
                    address == APPROVED_PUBLIC_TIP and name in ('tip_support.py','audit_release.py')):
                findings.append(dict(file=name,rule='unapproved-address'))
        for value in re.findall(re.escape('__cookie__'+':')+r'([^\s\x27\x22]+)',text):
            if not name.startswith('test_') or value not in ('inert-test','inert'):
                findings.append(dict(file=name,rule='cookie-shaped-content'))
        if name.endswith(('.py','.pyw')):ast.parse(text)
    report=dict(status='PASS' if not findings else 'FAIL',files=len(actual),findings=findings,
        hashes=hashes,scope='Allowlist, UTF-8/binary, syntax and listed secret/address patterns plus manual source review; not a guarantee against every possible secret.',
        proprietary_binaries_bundled=False,private_runtime_artifacts_allowed=False)
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json-report',type=Path)
    args=parser.parse_args();result=audit()
    if args.json_report:args.json_report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='hashes'},indent=2))
    return 0 if result['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
