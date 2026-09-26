from pathlib import Path

path = Path(__file__).resolve().parent / "app.py"
text = path.read_text(encoding="utf-8")

# Keep the backend online even if Render is briefly remounting/replacing /data.
# When the configured upload path is available it is used normally; otherwise
# the app falls back to local ephemeral storage for that boot instead of crashing.
text = text.replace(
    'UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", BASE_DIR / "uploads")).resolve()\nUPLOAD_DIR.mkdir(parents=True, exist_ok=True)',
    '''_REQUESTED_UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", BASE_DIR / "uploads")).resolve()\ntry:\n    _REQUESTED_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)\n    UPLOAD_DIR = _REQUESTED_UPLOAD_DIR\nexcept OSError as exc:\n    UPLOAD_DIR = (BASE_DIR / "uploads").resolve()\n    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)\n    print(f"DocWallet storage fallback active: requested={_REQUESTED_UPLOAD_DIR} fallback={UPLOAD_DIR} error={type(exc).__name__}")''',
)

# PostgreSQL/SSL connections can be dropped transiently by the platform/database.
# Test pooled connections before reuse and recycle them periodically so a stale
# socket does not turn a valid authenticated request into an intermittent 500.
text = text.replace(
    'from sqlalchemy import text',
    'from sqlalchemy import text\nfrom sqlalchemy.exc import OperationalError',
)
text = text.replace(
    'app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False\napp.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024',
    'app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False\napp.config["SQLALCHEMY_ENGINE_OPTIONS"] = {\n    "pool_pre_ping": True,\n    "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE_SECONDS", "300")),\n}\napp.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024',
)

text = text.replace(
    "    metadata = db.Column(db.JSON, nullable=True)",
    "    details = db.Column(db.JSON, nullable=True)",
)

text = text.replace(
    "            metadata=metadata or {},",
    "            details=metadata or {},",
)

text = text.replace(
    "def current_user_from_request() -> Optional[User]:\n    auth = request.headers.get(\"Authorization\", \"\")\n    if not auth.startswith(\"Bearer \"):\n        return None\n    token = auth.replace(\"Bearer \", \"\", 1).strip()",
    "def current_user_from_request() -> Optional[User]:\n    auth = request.headers.get(\"Authorization\", \"\")\n    if not auth.startswith(\"Bearer \"):\n        query_session = request.args.get(\"s\") or \"\"\n        if query_session:\n            auth = \"Bearer \" + query_session\n        else:\n            return None\n    token = auth.replace(\"Bearer \", \"\", 1).strip()",
)

# Do not translate a transient database disconnect into a false authentication
# failure. Retry once with a fresh SQLAlchemy session; if the database is still
# unavailable, return 503 so clients preserve the valid local session.
text = text.replace(
    '''def current_user_from_request() -> Optional[User]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        query_session = request.args.get("s") or ""
        if query_session:
            auth = "Bearer " + query_session
        else:
            return None
    token = auth.replace("Bearer ", "", 1).strip()
    try:
        payload = decode_token(token)
    except Exception:
        return None
    return db.session.get(User, payload.get("sub"))


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user_from_request()
        if not user:
            return error_response("Token inválido ou ausente.", 401)
        request.user = user
        return fn(*args, **kwargs)
    return wrapper''',
    '''class TransientDatabaseError(Exception):
    pass


def _reset_db_session():
    try:
        db.session.remove()
    except Exception:
        pass


def current_user_from_request() -> Optional[User]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        query_session = request.args.get("s") or ""
        if query_session:
            auth = "Bearer " + query_session
        else:
            return None
    token = auth.replace("Bearer ", "", 1).strip()
    try:
        payload = decode_token(token)
    except Exception:
        return None

    user_id = payload.get("sub")
    try:
        return db.session.get(User, user_id)
    except OperationalError:
        _reset_db_session()
        try:
            return db.session.get(User, user_id)
        except OperationalError as exc:
            _reset_db_session()
            raise TransientDatabaseError() from exc


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            user = current_user_from_request()
        except TransientDatabaseError:
            return error_response("Banco temporariamente indisponível. Tente novamente em instantes.", 503)
        if not user:
            return error_response("Token inválido ou ausente.", 401)
        request.user = user
        return fn(*args, **kwargs)
    return wrapper''',
)

bridge = """

# BEGIN_DW_BRIDGE_INSTALL
from dw_bridge import install_bridge as _dw_install_bridge
_dw_install_bridge(app, db, User, hash_password, create_token, user_to_dict, error_response, audit)
# END_DW_BRIDGE_INSTALL
"""

if "BEGIN_DW_BRIDGE_INSTALL" not in text:
    text = text.replace('\n\nif __name__ == "__main__":', bridge + '\n\nif __name__ == "__main__":')

path.write_text(text, encoding="utf-8")
print("DocWallet startup patch applied.")
