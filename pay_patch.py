from pathlib import Path

path = Path(__file__).resolve().parent / 'app.py'
text = path.read_text(encoding='utf-8')

snippet = """

# BEGIN_DW_PAY_INSTALL
from dw_payments import install_payments as _dw_install_pay
_dw_install_pay(app, db, require_auth, error_response, audit)
# END_DW_PAY_INSTALL
"""

if 'BEGIN_DW_PAY_INSTALL' not in text:
    text = text.replace('\n\nif __name__ == "__main__":', snippet + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding='utf-8')
print('DocWallet pay patch applied.')
