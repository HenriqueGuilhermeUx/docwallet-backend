from pathlib import Path
import json
import os
import urllib.error
import urllib.request

root = Path(__file__).resolve().parent
path = root / 'app.py'
text = path.read_text(encoding='utf-8')

snippet = """

# BEGIN_DW_ICP_SIGNATURE_INSTALL
from dw_icp_signature import install_icp_signature as _dw_install_icp_signature
_dw_install_icp_signature(app, db, require_auth, error_response, audit)
# END_DW_ICP_SIGNATURE_INSTALL
"""

if 'BEGIN_DW_ICP_SIGNATURE_INSTALL' not in text:
    text = text.replace('\n\nif __name__ == "__main__":', snippet + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding='utf-8')

# Rest PKI Core accepts a signature-session document either by an existing
# document `id` OR by an inline `file`. DocWallet uploads the PDF inline, so
# sending both fields causes InvalidRequest: "Cannot specify both Id and File".
provider_path = root / 'dw_icp_signature.py'
provider_text = provider_path.read_text(encoding='utf-8')
old_document_header = '''        document = {
            "id": str(req["id"]),
            "file": {
'''
new_document_header = '''        document = {
            # Inline document: Rest PKI Core requires `file` without `id`.
            "file": {
'''
if old_document_header in provider_text:
    provider_text = provider_text.replace(old_document_header, new_document_header, 1)
    provider_path.write_text(provider_text, encoding='utf-8')
    print('DocWallet Rest PKI payload patch applied: inline file without document id.')
elif 'Inline document: Rest PKI Core requires `file` without `id`.' in provider_text:
    print('DocWallet Rest PKI payload patch already applied.')
else:
    print('DocWallet Rest PKI payload patch: expected block not found; leaving provider unchanged.')

print('DocWallet ICP signature patch applied.')


def probe_restpki_core():
    """Validate endpoint + API key without creating a billable signature transaction.

    We request an intentionally nonexistent signature-session ID. A 422
    SignatureSessionNotFound means the Core endpoint accepted the API key and
    processed the authenticated request. 401 means the key was rejected; 403
    means the key was recognized but lacks permission for this operation.
    The probe never creates a session and never logs the API key.
    """
    enabled = os.environ.get('ICP_SIGNATURE_ENABLED', 'false').lower() == 'true'
    provider = os.environ.get('ICP_SIGNATURE_PROVIDER', '').strip().lower()
    endpoint = os.environ.get('ICP_SIGNATURE_PROVIDER_BASE_URL', '').strip().rstrip('/')
    api_key = os.environ.get('ICP_SIGNATURE_API_KEY', '').strip()

    if not enabled or provider not in {'lacuna', 'restpki', 'rest_pki', 'restpki_core', 'lacuna_restpki'}:
        print('DocWallet Rest PKI probe: skipped (provider disabled or not Lacuna).')
        return
    if not endpoint or not api_key:
        print('DocWallet Rest PKI probe: NOT CONFIGURED (endpoint/API key missing).')
        return

    probe_id = '00000000-0000-0000-0000-000000000000'
    url = f'{endpoint}/api/signature-sessions/{probe_id}'
    req = urllib.request.Request(
        url,
        headers={
            'Accept': 'application/json',
            'Accept-Language': 'pt-BR',
            'X-Api-Key': api_key,
        },
        method='GET',
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            print(f'DocWallet Rest PKI probe: reachable/authenticated (HTTP {response.status}).')
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {}
        code = payload.get('code') or payload.get('errorCode') or ''
        if exc.code == 422 and code == 'SignatureSessionNotFound':
            print('DocWallet Rest PKI probe: OK - endpoint reachable and API key authenticated (SignatureSessionNotFound expected).')
        elif exc.code == 404:
            print('DocWallet Rest PKI probe: endpoint reachable; nonexistent session returned HTTP 404.')
        elif exc.code == 403:
            print('DocWallet Rest PKI probe: API key recognized but permission denied (HTTP 403).')
        elif exc.code == 401:
            print('DocWallet Rest PKI probe: API KEY REJECTED (HTTP 401).')
        else:
            safe_code = f' code={code}' if code else ''
            print(f'DocWallet Rest PKI probe: endpoint responded HTTP {exc.code}.{safe_code}')
    except urllib.error.URLError as exc:
        print(f'DocWallet Rest PKI probe: connection failed ({getattr(exc, "reason", "network error")}).')
    except Exception as exc:
        print(f'DocWallet Rest PKI probe: unexpected failure ({type(exc).__name__}).')


probe_restpki_core()
