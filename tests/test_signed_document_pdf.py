from pathlib import Path

source = Path('dw_signed_document.py').read_text(encoding='utf-8')
patch = Path('sign_patch.py').read_text(encoding='utf-8')

assert "@app.get('/api/signatures/<request_id>/document.pdf')" in source
assert "req['status'] != 'completed'" in source
assert "application/pdf" in source
assert "final_hash" in source
assert "signature_image" in source
assert 'BEGIN_DW_SIGNED_DOCUMENT_INSTALL' in patch
assert 'install_signed_document' in patch

print('Signed PDF export smoke: OK')
