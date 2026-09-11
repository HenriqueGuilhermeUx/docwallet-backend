from pathlib import Path

path = Path(__file__).resolve().parent / 'app.py'
text = path.read_text(encoding='utf-8')

snippet = """

# BEGIN_DW_ICP_SIGNATURE_INSTALL
from dw_icp_signature import install_icp_signature as _dw_install_icp_signature
_dw_install_icp_signature(app, db, require_auth, error_response, audit)
# END_DW_ICP_SIGNATURE_INSTALL
"""

if 'BEGIN_DW_ICP_SIGNATURE_INSTALL' not in text:
    text = text.replace('\n\nif __name__ == "__main__":', snippet + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding='utf-8')
print('DocWallet ICP signature patch applied.')
