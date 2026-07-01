from pathlib import Path

path = Path(__file__).resolve().parent / 'app.py'
text = path.read_text(encoding='utf-8')

snippet = """

# BEGIN_DW_LINKS_INSTALL
from dw_links import install_links as _dw_install_links
_dw_install_links(app, db, Document, require_auth, error_response, audit)
# END_DW_LINKS_INSTALL
"""

if 'BEGIN_DW_LINKS_INSTALL' not in text:
    text = text.replace('\n\nif __name__ == "__main__":', snippet + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding='utf-8')
print('DocWallet links patch applied.')
