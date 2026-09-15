from pathlib import Path

path = Path(__file__).resolve().parent / 'app.py'
text = path.read_text(encoding='utf-8')

snippet = """

# BEGIN_DW_NEXOFFICE_INSTALL
from dw_nexoffice import install_nexoffice_bridge as _dw_install_nexoffice_bridge
_dw_install_nexoffice_bridge(app, db, User, Document, require_auth, error_response, audit)
# END_DW_NEXOFFICE_INSTALL
"""

if 'BEGIN_DW_NEXOFFICE_INSTALL' not in text:
    text = text.replace('\n\nif __name__ == "__main__":', snippet + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding='utf-8')
print('DocWallet NexOffice bridge patch applied.')
