from pathlib import Path

path = Path(__file__).resolve().parent / 'app.py'
text = path.read_text(encoding='utf-8')

snippet = """

# BEGIN_DW_PAID_CERT_INSTALL
from dw_paid_cert import install_paid_certificates as _dw_install_paid_certificates
_dw_install_paid_certificates(app, db, require_auth, error_response, audit, Certificate, certificate_to_dict, create_certificate_id, normalize_hash)
# END_DW_PAID_CERT_INSTALL
"""

if 'BEGIN_DW_PAID_CERT_INSTALL' not in text:
    text = text.replace('\n\nif __name__ == "__main__":', snippet + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding='utf-8')
print('DocWallet paid certificate patch applied.')
