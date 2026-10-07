from pathlib import Path
import os
import secrets

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

# Optional bootstrap for isolated homologation environments only.
# Production remains opt-in because this does nothing unless the explicit
# MYDATAMED_BOOTSTRAP_OWNER=true flag is present.
if os.environ.get('MYDATAMED_BOOTSTRAP_OWNER', 'false').lower() == 'true':
    owner_id = os.environ.get('MYDATAMED_DOCWALLET_OWNER_USER_ID', '').strip()
    if not owner_id:
        raise RuntimeError('MYDATAMED_DOCWALLET_OWNER_USER_ID required for bootstrap')

    from app import app, db, User, hash_password

    with app.app_context():
        owner = db.session.get(User, owner_id)
        if owner is None:
            owner = User(
                id=owner_id,
                name=os.environ.get('MYDATAMED_BOOTSTRAP_OWNER_NAME', 'MyDataMed Concierge Homolog'),
                email=os.environ.get(
                    'MYDATAMED_BOOTSTRAP_OWNER_EMAIL',
                    'mydatamed-concierge-homolog@internal.invalid',
                ).strip().lower(),
                password_hash=hash_password(secrets.token_urlsafe(64)),
                plan='pro',
            )
            db.session.add(owner)
            db.session.commit()
            print('DocWallet MyDataMed homolog owner bootstrapped.')
        else:
            print('DocWallet MyDataMed homolog owner already present.')
