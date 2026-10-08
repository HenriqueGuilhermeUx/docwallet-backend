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

# Internal smoke test for isolated homologation only. It exercises the real
# service-to-service decorators and routes without exposing the service key.
if os.environ.get('MYDATAMED_SMOKE_TEST', 'false').lower() == 'true':
    from app import app

    service_key = os.environ.get('MYDATAMED_SERVICE_KEY', '').strip()
    if not service_key:
        raise RuntimeError('MYDATAMED_SERVICE_KEY required for smoke test')

    headers = {'X-MyDataMed-Key': service_key}
    client = app.test_client()

    health = client.get('/api/internal/mydatamed/concierge/health', headers=headers)
    health_body = health.get_json(silent=True) or {}
    if health.status_code != 200 or not health_body.get('configured'):
        raise RuntimeError(
            f'MyDataMed bridge health smoke failed: status={health.status_code} body={health_body}'
        )

    smoke_payload = {
        'documentType': 'combined_onboarding',
        'externalReference': 'homolog-smoke-v1',
        'title': 'MyDataMed Concierge Homolog Smoke',
        'content': (
            'Documento técnico de homologação do MyDataMed Concierge para validar '
            'criação de solicitação, política de evidência verificada, idempotência '
            'e consulta de status no bridge DocWallet. Não possui efeito contratual.'
        ),
        'signer': {
            'name': 'MyDataMed Concierge Homolog',
            'email': 'concierge-homolog-smoke@internal.invalid',
        },
    }
    create_headers = {
        **headers,
        'X-Idempotency-Key': 'mydatamed-render-homolog-smoke-v1',
    }
    created = client.post(
        '/api/internal/mydatamed/concierge/signatures',
        headers=create_headers,
        json=smoke_payload,
    )
    created_body = created.get_json(silent=True) or {}
    request_id = str((created_body.get('request') or {}).get('id') or '')
    if (
        created.status_code not in (200, 201)
        or not created_body.get('success')
        or created_body.get('requiredEvidence') != 'verified_evidence'
        or not request_id
    ):
        raise RuntimeError(
            f'MyDataMed bridge create smoke failed: status={created.status_code} body={created_body}'
        )

    status = client.get(
        f'/api/internal/mydatamed/concierge/signatures/{request_id}',
        headers=headers,
    )
    status_body = status.get_json(silent=True) or {}
    returned_id = str((status_body.get('request') or {}).get('id') or '')
    if status.status_code != 200 or not status_body.get('success') or returned_id != request_id:
        raise RuntimeError(
            f'MyDataMed bridge status smoke failed: status={status.status_code} body={status_body}'
        )

    print(
        'DocWallet MyDataMed bridge smoke test PASS: '
        'health/create/status + verified_evidence.'
    )
