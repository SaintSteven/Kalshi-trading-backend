from pathlib import Path
import base64, zlib, runpy, sys, tempfile

HERE = Path(__file__).resolve().parent
parts = sorted((HERE / "payload").glob("v010_*.part"))
if not parts:
    raise SystemExit("NFL v0.10 payload parts are missing")
blob = "".join(p.read_text() for p in parts)
source = zlib.decompress(base64.b85decode(blob.encode()))
with tempfile.TemporaryDirectory() as td:
    script = Path(td) / "nfl_receiving_backtest_v0_10.py"
    script.write_bytes(source)
    sys.argv[0] = str(script)
    runpy.run_path(str(script), run_name="__main__")
