from pathlib import Path

path = Path(__file__).resolve().parent / 'app.py'
text = path.read_text(encoding='utf-8')

snippet = """

# BEGIN_DW_DOCFLOW_INSTALL
from dw_docflow import install_docflow as _dw_install_docflow
_dw_install_docflow(app, db, Document, require_auth, error_response, audit)
# END_DW_DOCFLOW_INSTALL
"""

if 'BEGIN_DW_DOCFLOW_INSTALL' not in text:
    if 'BEGIN_DW_INTELLIGENCE_INSTALL' in text:
        text = text.replace('# END_DW_INTELLIGENCE_INSTALL\n', '# END_DW_INTELLIGENCE_INSTALL\n' + snippet + '\n')
    else:
        text = text.replace('\n\nif __name__ == "__main__":', snippet + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding='utf-8')
print('DocFlow patch applied.')
