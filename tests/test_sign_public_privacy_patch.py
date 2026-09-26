from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_sign_startup_patch_minimizes_public_signature_payload(tmp_path):
    for name in ('app.py', 'dw_sign.py', 'sign_patch.py'):
        shutil.copy2(ROOT / name, tmp_path / name)

    subprocess.run([sys.executable, str(tmp_path / 'sign_patch.py')], cwd=tmp_path, check=True)
    patched = (tmp_path / 'dw_sign.py').read_text(encoding='utf-8')

    assert 'def pack_request(req, parties=None, public=False):' in patched
    assert "pack_request(req, parties, public=True)" in patched
    assert "'next_party': None" in patched
    assert "'parties': [\n                ({'name': p.name, 'status': p.status, 'signed_at': iso(p.signed_at)} if public else pack_party(p))" in patched

    # Public serializers must not expose the internal party id or a later party's
    # bearer link. Authenticated serializers still retain those fields.
    assert "out = {\n            'name': p.name," in patched
    assert "if not public:\n            out.update({\n                'id': p.id," in patched
    assert "'next_party': pack_next_party(next_party)" not in patched


def test_sign_startup_patch_is_idempotent(tmp_path):
    for name in ('app.py', 'dw_sign.py', 'sign_patch.py'):
        shutil.copy2(ROOT / name, tmp_path / name)

    command = [sys.executable, str(tmp_path / 'sign_patch.py')]
    subprocess.run(command, cwd=tmp_path, check=True)
    subprocess.run(command, cwd=tmp_path, check=True)
