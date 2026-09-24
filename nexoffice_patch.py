import os
import runpy
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

# One-time, non-blocking database compaction migration.
# The source remains DATABASE_URL and the destination is TARGET_DATABASE_URL.
# Any failure is logged but never prevents the production backend from starting.
if os.environ.get('RUN_COMPACT_MIGRATION', 'false').lower() == 'true':
    migration_script = Path(__file__).resolve().parent / 'ops' / 'migrate_postgres_compact.py'
    print('DocWallet compact database migration requested.')
    try:
        runpy.run_path(str(migration_script), run_name='__main__')
        print('DocWallet compact database migration completed successfully.')
    except SystemExit as exc:
        print(f'DocWallet compact database migration exited with code/message: {exc}')
    except Exception as exc:
        print(f'DocWallet compact database migration failed safely: {type(exc).__name__}: {exc}')
