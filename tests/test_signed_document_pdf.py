from pathlib import Path


def test_signed_document_module_exists_and_registers_pdf_route():
    source = Path('dw_signed_document.py').read_text(encoding='utf-8')
    assert "@app.get('/api/signatures/<request_id>/document.pdf')" in source
    assert "req['status'] != 'completed'" in source
    assert "application/pdf" in source
    assert "final_hash" in source
    assert "signature_image" in source
