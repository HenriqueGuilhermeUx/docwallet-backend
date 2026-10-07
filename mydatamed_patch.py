from pathlib import Path

path = Path(__file__).resolve().parent / 'app.py'
text = path.read_text(encoding='utf-8')

snippet = """

# BEGIN_DW_MYDATAMED_CONCIERGE_INSTALL
from dw_mydatamed_concierge import install_mydatamed_concierge_bridge as _dw_install_mydatamed_concierge_bridge
_dw_install_mydatamed_concierge_bridge(app, db, User, error_response, audit)
# END_DW_MYDATAMED_CONCIERGE_INSTALL
"""

if 'BEGIN_DW_MYDATAMED_CONCIERGE_INSTALL' not in text:
    text = text.replace('\n\nif __name__ == "__main__":', snippet + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding='utf-8')
print('DocWallet MyDataMed Concierge bridge patch applied.')
