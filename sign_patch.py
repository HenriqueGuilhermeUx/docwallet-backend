from pathlib import Path

path = Path(__file__).resolve().parent / 'app.py'
text = path.read_text(encoding='utf-8')

snippet = """

# BEGIN_DW_SIGN_INSTALL
from dw_sign import install_sign as _dw_install_sign
_dw_install_sign(app, db, require_auth, error_response, audit)
# END_DW_SIGN_INSTALL
"""

if 'BEGIN_DW_SIGN_INSTALL' not in text:
    text = text.replace('\n\nif __name__ == "__main__":', snippet + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding='utf-8')
print('DocWallet sign patch applied.')
