"""Recover and inspect the exact v0.10 model source used by the successful historical workflow.
This does not execute betting logic or place orders.
"""
import ast, base64, hashlib, json, urllib.request, zlib
from pathlib import Path

GOOD_COMMIT='1c5918dcdada82d0bcbc69cf2bd400341f1ddd71'
BASE=f'https://raw.githubusercontent.com/SaintSteven/Kalshi-trading-backend/{GOOD_COMMIT}/research/nfl_receiving_v0_10/payload'
parts=[]
for name in ('b64_00.part','b64_01.part','b64_02.part'):
    with urllib.request.urlopen(f'{BASE}/{name}', timeout=30) as r:
        parts.append(r.read().decode('ascii').strip())
blob=''.join(parts)
source=zlib.decompress(base64.b64decode(blob.encode('ascii'))).decode('utf-8')
sha=hashlib.sha256(source.encode()).hexdigest()
Path('recovered_model.py').write_text(source)
tree=ast.parse(source)
print('RECOVERY_STATUS: PASS')
print('SOURCE_COMMIT:',GOOD_COMMIT)
print('MODEL_SOURCE_SHA256:',sha)
print('SOURCE_BYTES:',len(source.encode()))
print('\n### Functions/classes')
for node in tree.body:
    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
        print(node.name, node.lineno, node.end_lineno)
print('\n### Likely inference/calibration interfaces')
keys=('project','predict','model','calibr','feature','prob','monte','simulate','schedule','load')
for node in tree.body:
    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and any(k in node.name.lower() for k in keys):
        print('\n##',node.name)
        print(ast.get_source_segment(source,node)[:12000])
print('\n### CLI/output references')
for line in source.splitlines():
    if any(k in line for k in ('add_argument','to_csv(','read_csv(','nflverse','github.com','validation_seasons','simulations')):
        print(line[:500])
