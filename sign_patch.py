from pathlib import Path

path = Path(__file__).resolve().parent / 'app.py'
text = path.read_text(encoding='utf-8')

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

if 'BEGIN_DW_SIGN_INSTALL' not in text:
    text = text.replace('\n\nif __name__ == "__main__":', sign_snippet + '\n\nif __name__ == "__main__":')

if 'BEGIN_DW_SIGNATURE_DELIVERY_INSTALL' not in text:
    if '# END_DW_SIGN_INSTALL' in text:
        text = text.replace('# END_DW_SIGN_INSTALL', '# END_DW_SIGN_INSTALL' + delivery_snippet)
    else:
        text = text.replace('\n\nif __name__ == "__main__":', delivery_snippet + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding='utf-8')
print('DocWallet sign patch applied.')
