from pathlib import Path

ROOT = Path(__file__).resolve().parent
path = ROOT / 'app.py'
text = path.read_text(encoding='utf-8')


def _replace_required(source, old, new, label):
    """Apply an idempotent startup hardening replacement or fail closed."""
    if new in source:
        return source
    if old not in source:
        raise RuntimeError(f'DocWallet sign privacy patch target not found: {label}')
    return source.replace(old, new, 1)


# The project currently applies runtime compatibility patches before importing the
# Flask app. Keep public signature links privacy-safe in that same startup phase:
# public signers must never receive another party's bearer code, CPF, phone, IP,
# geolocation, device fingerprint or evidence package.
sign_path = ROOT / 'dw_sign.py'
sign_text = sign_path.read_text(encoding='utf-8')

sign_text = _replace_required(
    sign_text,
    "        out = {\n            'id': p.id,\n            'name': p.name,",
    "        out = {\n            'name': p.name,",
    'remove public party id',
)
sign_text = _replace_required(
    sign_text,
    "        if not public:\n            out.update({\n                'phone': p.phone,",
    "        if not public:\n            out.update({\n                'id': p.id,\n                'phone': p.phone,",
    'keep party id on authenticated payloads only',
)
sign_text = _replace_required(
    sign_text,
    "    def pack_request(req, parties=None):",
    "    def pack_request(req, parties=None, public=False):",
    'public request serializer flag',
)
sign_text = _replace_required(
    sign_text,
    "            'parties': [pack_party(p) for p in parties],",
    "            'parties': [\n                ({'name': p.name, 'status': p.status, 'signed_at': iso(p.signed_at)} if public else pack_party(p))\n                for p in parties\n            ],",
    'minimal public party list',
)
sign_text = _replace_required(
    sign_text,
    "        return jsonify({'success': True, 'request': pack_request(req, parties), 'party': pack_party(party, public=True), 'contract_content': req.contract_content})",
    "        return jsonify({'success': True, 'request': pack_request(req, parties, public=True), 'party': pack_party(party, public=True), 'contract_content': req.contract_content})",
    'public signature GET serializer',
)
sign_text = _replace_required(
    sign_text,
    "        next_party = SignatureParty.query.filter(SignatureParty.request_id == req.id, SignatureParty.status != 'signed').order_by(SignatureParty.id).first()\n        return jsonify({'success': True, 'request': pack_request(req, parties), 'party': pack_party(party, public=True), 'next_party': pack_next_party(next_party)})",
    "        return jsonify({'success': True, 'request': pack_request(req, parties, public=True), 'party': pack_party(party, public=True), 'next_party': None})",
    'do not disclose next signer bearer link',
)

sign_path.write_text(sign_text, encoding='utf-8')

sign_snippet = """

# BEGIN_DW_SIGN_INSTALL
from dw_sign import install_sign as _dw_install_sign
_dw_install_sign(app, db, require_auth, error_response, audit)
# END_DW_SIGN_INSTALL
"""

delivery_snippet = """

# BEGIN_DW_SIGNATURE_DELIVERY_INSTALL
from dw_signature_delivery import install_signature_delivery as _dw_install_signature_delivery
_dw_install_signature_delivery(app, db, require_auth, error_response, audit)
# END_DW_SIGNATURE_DELIVERY_INSTALL
"""

identity_snippet = """

# BEGIN_DW_SIGNATURE_IDENTITY_INSTALL
from dw_signature_identity import install_signature_identity as _dw_install_signature_identity
_dw_install_signature_identity(app, db, error_response, audit)
# END_DW_SIGNATURE_IDENTITY_INSTALL
"""

signed_document_snippet = """

# BEGIN_DW_SIGNED_DOCUMENT_INSTALL
from dw_signed_document import install_signed_document as _dw_install_signed_document
_dw_install_signed_document(app, db, require_auth, error_response, audit)
# END_DW_SIGNED_DOCUMENT_INSTALL
"""

if 'BEGIN_DW_SIGN_INSTALL' not in text:
    text = text.replace('\n\nif __name__ == "__main__":', sign_snippet + '\n\nif __name__ == "__main__":')

if 'BEGIN_DW_SIGNATURE_DELIVERY_INSTALL' not in text:
    if '# END_DW_SIGN_INSTALL' in text:
        text = text.replace('# END_DW_SIGN_INSTALL', '# END_DW_SIGN_INSTALL' + delivery_snippet)
    else:
        text = text.replace('\n\nif __name__ == "__main__":', delivery_snippet + '\n\nif __name__ == "__main__":')

if 'BEGIN_DW_SIGNATURE_IDENTITY_INSTALL' not in text:
    if '# END_DW_SIGNATURE_DELIVERY_INSTALL' in text:
        text = text.replace('# END_DW_SIGNATURE_DELIVERY_INSTALL', '# END_DW_SIGNATURE_DELIVERY_INSTALL' + identity_snippet)
    elif '# END_DW_SIGN_INSTALL' in text:
        text = text.replace('# END_DW_SIGN_INSTALL', '# END_DW_SIGN_INSTALL' + identity_snippet)
    else:
        text = text.replace('\n\nif __name__ == "__main__":', identity_snippet + '\n\nif __name__ == "__main__":')

if 'BEGIN_DW_SIGNED_DOCUMENT_INSTALL' not in text:
    if '# END_DW_SIGNATURE_IDENTITY_INSTALL' in text:
        text = text.replace('# END_DW_SIGNATURE_IDENTITY_INSTALL', '# END_DW_SIGNATURE_IDENTITY_INSTALL' + signed_document_snippet)
    elif '# END_DW_SIGNATURE_DELIVERY_INSTALL' in text:
        text = text.replace('# END_DW_SIGNATURE_DELIVERY_INSTALL', '# END_DW_SIGNATURE_DELIVERY_INSTALL' + signed_document_snippet)
    elif '# END_DW_SIGN_INSTALL' in text:
        text = text.replace('# END_DW_SIGN_INSTALL', '# END_DW_SIGN_INSTALL' + signed_document_snippet)
    else:
        text = text.replace('\n\nif __name__ == "__main__":', signed_document_snippet + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding='utf-8')
print('DocWallet sign patch applied.')
