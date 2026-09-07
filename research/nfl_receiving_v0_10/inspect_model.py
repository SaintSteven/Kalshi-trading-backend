"""Read-only integrity audit of the preserved NFL receiving model."""
import ast, base64, zlib, hashlib, json
from pathlib import Path
p=Path(__file__).resolve().parent
parts=sorted((p/'payload').glob('v010_*.part'))
report={'parts':len(parts),'status':'UNVERIFIED'}
try:
 blob=''.join(x.read_text() for x in parts)
 source=zlib.decompress(base64.b85decode(blob.encode())).decode()
 tree=ast.parse(source)
 report.update(status='SOURCE_VALID',sha256=hashlib.sha256(source.encode()).hexdigest())
 with open('model_audit.txt','w') as f:
  f.write(json.dumps(report,indent=2)+'\n')
  for node in tree.body:
   if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
    f.write('\n### '+node.name+'\n')
    if any(k in node.name.lower() for k in ['project','predict','model','calibr','feature','load','schedule','main']):f.write(ast.get_source_segment(source,node)[:16000]+'\n')
  f.write('\n### CLI and data references\n')
  for line in source.splitlines():
   if any(k in line for k in ['add_argument','to_csv(','read_csv(','nflverse','github.com','2026','calibration']):f.write(line[:500]+'\n')
except Exception as e:
 report.update(status='SOURCE_INTEGRITY_FAILURE',error=type(e).__name__+': '+str(e))
 Path('model_audit.txt').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
if report['status']!='SOURCE_VALID':raise SystemExit('Preserved model cannot be used for prospective inference; no probabilities generated.')
