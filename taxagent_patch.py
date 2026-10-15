from pathlib import Path

path = Path(__file__).resolve().parent / 'app.py'
text = path.read_text(encoding='utf-8')

snippet = """

# BEGIN_DW_TAXAGENT_EVIDENCE_INSTALL
from dw_taxagent import install_taxagent_bridge as _dw_install_taxagent_bridge
_dw_install_taxagent_bridge(app, db, User, Document, error_response, audit, hash_password, UPLOAD_DIR)
# END_DW_TAXAGENT_EVIDENCE_INSTALL
"""

if 'BEGIN_DW_TAXAGENT_EVIDENCE_INSTALL' not in text:
    text = text.replace('\n\nif __name__ == "__main__":', snippet + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding='utf-8')
print('DocWallet TaxAgent evidence bridge patch applied.')
