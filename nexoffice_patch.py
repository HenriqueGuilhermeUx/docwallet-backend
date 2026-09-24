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

# Runtime-only database switch. This keeps the original DATABASE_URL untouched
# so rollback is immediate: USE_COMPACT_DATABASE=false returns to the old DB.
if 'BEGIN_DW_COMPACT_DB_SWITCH' not in text:
    old_database_line = 'DATABASE_URL = os.environ.get("DATABASE_URL")'
    compact_database_block = '''# BEGIN_DW_COMPACT_DB_SWITCH
_USE_COMPACT_DATABASE = os.environ.get("USE_COMPACT_DATABASE", "false").lower() == "true"
DATABASE_URL = (
    os.environ.get("TARGET_DATABASE_URL")
    if _USE_COMPACT_DATABASE and os.environ.get("TARGET_DATABASE_URL")
    else os.environ.get("DATABASE_URL")
)
# END_DW_COMPACT_DB_SWITCH'''
    if old_database_line in text:
        text = text.replace(old_database_line, compact_database_block, 1)
    else:
        print('DocWallet compact DB switch marker not installed: DATABASE_URL line not found.')

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
