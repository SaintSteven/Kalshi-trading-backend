"""Inspect the preserved model without executing historical betting code."""
import ast, base64, zlib, json
from pathlib import Path
p=Path(__file__).resolve().parent
source=zlib.decompress(base64.b85decode(''.join(x.read_text() for x in sorted((p/'payload').glob('v010_*.part'))).encode())).decode()
tree=ast.parse(source)
print('MODEL SOURCE SHA256:',__import__('hashlib').sha256(source.encode()).hexdigest())
for node in tree.body:
 if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
  print('\n###',node.name,'lines',node.lineno,'-',node.end_lineno)
  print(ast.get_source_segment(source,node)[:16000] if any(k in node.name.lower() for k in ['project','predict','model','calibr','feature','load','schedule','main']) else ast.get_source_segment(source,node).split('\n')[0])
print('\n### CLI and output references')
for line in source.splitlines():
 if any(k in line for k in ['add_argument','to_csv(','read_csv(','nflverse','github.com','2026','calibration']):print(line[:500])
