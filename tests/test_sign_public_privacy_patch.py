from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def _copy_runtime(target: Path):
    for name in ('app.py', 'dw_sign.py', 'sign_patch.py'):
        shutil.copy2(ROOT / name, target / name)


def _apply_patch(target: Path):
    subprocess.run([sys.executable, str(target / 'sign_patch.py')], cwd=target, check=True)
    return (target / 'dw_sign.py').read_text(encoding='utf-8')


def test_public_signature_payload_is_minimized():
    with tempfile.TemporaryDirectory() as raw:
        target = Path(raw)
        _copy_runtime(target)
        patched = _apply_patch(target)

        assert 'def pack_request(req, parties=None, public=False):' in patched
        assert "pack_request(req, parties, public=True)" in patched
        assert "'next_party': None" in patched
        assert "'parties': [\n                ({'name': p.name, 'status': p.status, 'signed_at': iso(p.signed_at)} if public else pack_party(p))" in patched

        # Public serializers must not expose internal party IDs or another
        # signer's bearer URL. Authenticated serializers retain their IDs.
        assert "out = {\n            'name': p.name," in patched
        assert "if not public:\n            out.update({\n                'id': p.id," in patched
        assert "'next_party': pack_next_party(next_party)" not in patched


def test_sign_patch_is_idempotent():
    with tempfile.TemporaryDirectory() as raw:
        target = Path(raw)
        _copy_runtime(target)
        first = _apply_patch(target)
        second = _apply_patch(target)
        assert first == second


if __name__ == '__main__':
    test_public_signature_payload_is_minimized()
    test_sign_patch_is_idempotent()
    print('Sign public privacy smoke: OK')
