from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

# Render runs this compatibility patch before importing app.py. The smoke test
# must mirror that startup sequence so it exercises the same runtime shape.
runpy.run_path(str(ROOT / "fix_sqlalchemy.py"), run_name="__main__")

spec = spec_from_file_location("_docwallet_runtime_app", ROOT / "app.py")
if spec is None or spec.loader is None:
    raise RuntimeError("Could not load DocWallet app.py")
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
app = module.app
