"""Restricted, ephemeral iOS QA entrypoint.

Requires dedicated SQLite and explicit staging access key. Does not modify
production app.py, runtime patches, external bridges or deployment settings.
"""
import hmac
import os
from pathlib import Path

from werkzeug.wrappers import Response


def _configuration():
    if os.environ.get("DOCWALLET_IOS_STAGING_ONLY") != "1":
        raise RuntimeError("iOS staging entrypoint requires explicit opt-in")
    database = os.environ.get("DATABASE_URL", "")
    if not database.startswith("sqlite:////tmp/docwallet-ios-qa-"):
        raise RuntimeError("Refusing non-ephemeral or production database")
    uploads = Path(os.environ.get("UPLOAD_DIR", "")).resolve()
    if not str(uploads).startswith("/tmp/docwallet-ios-qa-"):
        raise RuntimeError("Refusing non-staging file storage")
    key = os.environ.get("DOCWALLET_IOS_STAGING_KEY", "")
    if len(key) < 32:
        raise RuntimeError("A dedicated 32+ character staging access key is required")
    jwt_secret = os.environ.get("JWT_SECRET", "")
    if len(jwt_secret) < 32 or jwt_secret == key:
        raise RuntimeError("Dedicated signing secret is missing")
    for flag in ("NEXA_SSO_ENABLED", "NEXA_ECOSYSTEM_ATTENTION_ENABLED",
                 "NEXOFFICE_BRIDGE_ENABLED", "DOCFLOW_EXTERNAL_INTEGRATIONS_ENABLED",
                 "DOCUMENT_INTELLIGENCE_ALLOW_EXTERNAL"):
        if os.environ.get(flag, "false").lower() != "false":
            raise RuntimeError("External integration must stay disabled: " + flag)
    return key


_STAGING_ACCESS_KEY = _configuration()
from app import app as flask_app  # noqa: E402


class StagingAccessGuard:
    def __init__(self, backend):
        self.backend = backend

    def __call__(self, environ, start_response):
        if environ.get("PATH_INFO") == "/api/health":
            return Response(
                '{"status":"staging","isolated":true}', status=200,
                content_type="application/json",
                headers={"Cache-Control": "no-store"},
            )(environ, start_response)
        candidate = environ.get("HTTP_X_DOCWALLET_STAGING_KEY", "")
        if not candidate or not hmac.compare_digest(candidate, _STAGING_ACCESS_KEY):
            return Response("Not Found", status=404, content_type="text/plain",
                            headers={"Cache-Control": "no-store"})(environ, start_response)
        return self.backend.wsgi_app(environ, start_response)


app = StagingAccessGuard(flask_app)
