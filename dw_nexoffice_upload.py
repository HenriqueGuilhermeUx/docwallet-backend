def install_nexoffice_upload_bridge(app, db, User, Document, fail, log):
    """Secure NexOffice bridge extensions for direct upload and contracts.

    Raw document bytes stay in DocWallet. NexOffice receives references/metadata only.
    Production requires an explicit active DocWallet workspace link. A synthetic link
    can be enabled only in isolated homologation with NEXOFFICE_HOMOLOG_AUTO_LINK=true.
    """
    import datetime as dt
    import hashlib
    import hmac
    import json
    import os
    import uuid
    from functools import wraps
    from pathlib import Path

    import bcrypt
    from flask import jsonify, request
    from sqlalchemy import text
    from werkzeug.utils import secure_filename

    enabled = os.environ.get("NEXOFFICE_BRIDGE_ENABLED", "true").lower() == "true"
    service_key = os.environ.get("NEXOFFICE_SERVICE_KEY", "").strip()
    homolog_auto_link = os.environ.get("NEXOFFICE_HOMOLOG_AUTO_LINK", "false").lower() == "true"
    upload_dir = Path(os.environ.get("UPLOAD_DIR", Path(__file__).resolve().parent / "uploads")).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    max_upload_mb = max(1, int(os.environ.get("MAX_UPLOAD_MB", "25") or 25))
    allowed_extensions = {"pdf", "png", "jpg", "jpeg", "txt"}
    allowed_mimes = {"application/pdf", "image/png", "image/jpeg", "text/plain", "application/octet-stream"}

    def supplied_service_key():
        direct = request.headers.get("X-NexOffice-Key", "").strip()
        if direct:
            return direct
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth.split(" ", 1)[1].strip()
        return ""

    def require_service(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not enabled:
                return fail("NexOffice bridge desabilitado.", 503, {"code": "bridge_disabled"})
            if not service_key:
                return fail("NexOffice bridge não configurado.", 503, {"code": "service_key_not_configured"})
            supplied = supplied_service_key()
            if not supplied or not hmac.compare_digest(supplied, service_key):
                return fail("Credencial NexOffice inválida.", 401, {"code": "invalid_service_key"})
            return fn(*args, **kwargs)
        return wrapper

    def workspace_id_from_request():
        value = (request.headers.get("X-NexOffice-Workspace-ID") or "").strip()
        if not value:
            return None, "workspace_required"
        try:
            uuid.UUID(value)
            return value, None
        except Exception:
            return None, "invalid_workspace"

    def ensure_homolog_link(workspace_id):
        if not homolog_auto_link:
            return None
        email = f"nexoffice-homolog+{workspace_id}@local.invalid"
        user = User.query.filter_by(email=email).first()
        if not user:
            password = uuid.uuid4().hex.encode("utf-8")
            user = User(
                name="NexOffice Homolog",
                email=email,
                password_hash=bcrypt.hashpw(password, bcrypt.gensalt()).decode("utf-8"),
                plan="pro",
            )
            db.session.add(user)
            db.session.flush()
        existing = db.session.execute(
            text("select id from nexoffice_connections where workspace_id=:workspace_id and user_id=:user_id limit 1"),
            {"workspace_id": workspace_id, "user_id": user.id},
        ).mappings().first()
        now = dt.datetime.utcnow()
        if existing:
            db.session.execute(
                text("update nexoffice_connections set status='active',revoked_at=null,updated_at=:now,metadata_json=:metadata where id=:id"),
                {"id": existing["id"], "now": now, "metadata": json.dumps({"source": "homolog_auto_link"})},
            )
        else:
            db.session.execute(
                text("insert into nexoffice_connections(id,workspace_id,user_id,status,metadata_json,connected_at,revoked_at,updated_at) values(:id,:workspace_id,:user_id,'active',:metadata,:now,null,:now)"),
                {"id": str(uuid.uuid4()), "workspace_id": workspace_id, "user_id": user.id, "metadata": json.dumps({"source": "homolog_auto_link"}), "now": now},
            )
        db.session.commit()
        return user

    def linked_user(workspace_id):
        rows = db.session.execute(
            text(
                "select user_id from nexoffice_connections "
                "where workspace_id=:workspace_id and status='active' "
                "order by updated_at desc limit 2"
            ),
            {"workspace_id": workspace_id},
        ).mappings().all()
        if not rows:
            user = ensure_homolog_link(workspace_id)
            if user:
                return user, None
            return None, "workspace_not_connected"
        if len(rows) > 1:
            return None, "workspace_connection_ambiguous"
        user = db.session.get(User, rows[0]["user_id"])
        if not user:
            return None, "workspace_owner_missing"
        return user, None

    def workspace_owner_or_error(workspace_id):
        owner, owner_error = linked_user(workspace_id)
        if owner_error == "workspace_not_connected":
            return None, fail("Conecte a DocWallet a este workspace antes de usar esta função.", 409, {"code": owner_error})
        if owner_error == "workspace_connection_ambiguous":
            return None, fail("Há mais de uma conta DocWallet vinculada a este workspace. Defina uma conta documental principal.", 409, {"code": owner_error})
        if owner_error:
            return None, fail("A conta DocWallet vinculada não está disponível.", 409, {"code": owner_error})
        return owner, None

    def unpack_response(response):
        status = 200
        body = response
        if isinstance(response, tuple):
            body = response[0]
            if len(response) > 1 and isinstance(response[1], int):
                status = response[1]
        elif hasattr(response, "status_code"):
            status = int(response.status_code)
        payload = None
        if hasattr(body, "get_json"):
            try:
                payload = body.get_json(silent=True)
            except TypeError:
                payload = body.get_json()
        if payload is None:
            payload = {"success": 200 <= status < 300}
        return payload, status

    def invoke_user_view(endpoint, user, *args):
        view = app.view_functions.get(endpoint)
        if not view:
            return None, 503
        original = getattr(view, "__wrapped__", None)
        if original:
            request.user = user
            return unpack_response(original(*args))
        return unpack_response(view(*args))

    def sha256_file(path):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @app.get("/api/internal/nexoffice/contracts/templates")
    @require_service
    def nexoffice_contract_templates():
        workspace_id, workspace_error = workspace_id_from_request()
        if workspace_error:
            return fail("Workspace NexOffice ausente ou inválido.", 400, {"code": workspace_error})
        _owner, denied = workspace_owner_or_error(workspace_id)
        if denied:
            return denied
        view = app.view_functions.get("contract_templates")
        if not view:
            return fail("Modelos de contrato indisponíveis na DocWallet.", 503, {"code": "contract_templates_unavailable"})
        payload, status = unpack_response(view())
        if status >= 300:
            return jsonify(payload), status
        return jsonify({"success": True, "templates": payload.get("templates") or []})

    @app.post("/api/internal/nexoffice/contracts/create")
    @require_service
    def nexoffice_contract_create():
        workspace_id, workspace_error = workspace_id_from_request()
        if workspace_error:
            return fail("Workspace NexOffice ausente ou inválido.", 400, {"code": workspace_error})
        owner, denied = workspace_owner_or_error(workspace_id)
        if denied:
            return denied
        payload, status = invoke_user_view("create_contract", owner)
        if status >= 300 or not isinstance(payload, dict):
            return jsonify(payload or {"success": False, "error": "contract_create_failed"}), status
        contract = payload.get("contract") or {}
        contract_id = str(contract.get("id") or "")
        if not contract_id:
            return fail("Contrato criado sem referência válida.", 502, {"code": "invalid_contract_response"})
        document_payload, document_status = invoke_user_view("save_contract_as_document", owner, contract_id)
        if document_status >= 300 or not isinstance(document_payload, dict):
            return jsonify(document_payload or {"success": False, "error": "contract_document_failed"}), document_status
        document = document_payload.get("document") or {}
        return jsonify({"success": True, "contract": contract, "document": document}), 201

    @app.post("/api/internal/nexoffice/documents/upload")
    @require_service
    def nexoffice_upload_document():
        workspace_id, workspace_error = workspace_id_from_request()
        if workspace_error:
            return fail("Workspace NexOffice ausente ou inválido.", 400, {"code": workspace_error})
        owner, denied = workspace_owner_or_error(workspace_id)
        if denied:
            return denied

        file = request.files.get("file")
        if not file or not file.filename:
            return fail("Arquivo não enviado.", 400, {"code": "file_required"})

        original = secure_filename(file.filename)
        if "." not in original:
            return fail("Formato de arquivo inválido.", 400, {"code": "invalid_file_extension"})
        extension = original.rsplit(".", 1)[1].lower()
        if extension not in allowed_extensions:
            return fail("Formato não suportado. Use PDF, JPG, PNG ou TXT.", 400, {"code": "unsupported_file_type"})
        if file.mimetype and file.mimetype not in allowed_mimes:
            return fail("Tipo MIME não suportado.", 400, {"code": "unsupported_mime_type"})

        display_name = (request.form.get("name") or original).strip()[:255] or original
        doc_type = (request.form.get("type") or "document").strip()[:80] or "document"
        category = (request.form.get("category") or "signature").strip()[:80] or "signature"

        stored_filename = f"{uuid.uuid4().hex}.{extension}"
        owner_dir = upload_dir / owner.id
        owner_dir.mkdir(parents=True, exist_ok=True)
        file_path = owner_dir / stored_filename
        file.save(file_path)

        file_size = file_path.stat().st_size
        if file_size <= 0:
            file_path.unlink(missing_ok=True)
            return fail("O arquivo enviado está vazio.", 400, {"code": "empty_file"})
        if file_size > max_upload_mb * 1024 * 1024:
            file_path.unlink(missing_ok=True)
            return fail(f"Arquivo acima do limite de {max_upload_mb} MB.", 413, {"code": "file_too_large"})

        file_hash = sha256_file(file_path)
        document = Document(
            user_id=owner.id,
            name=display_name,
            original_filename=original,
            stored_filename=stored_filename,
            file_path=str(file_path),
            file_type=file.mimetype or "application/octet-stream",
            file_size=file_size,
            file_hash=file_hash,
            doc_type=doc_type,
            category=category,
        )
        db.session.add(document)
        db.session.commit()
        log(
            "document.upload.nexoffice",
            owner.id,
            "document",
            document.id,
            {"workspace_id": workspace_id, "file_hash": file_hash, "file_size": file_size},
        )

        return jsonify(
            {
                "success": True,
                "document": {
                    "id": document.id,
                    "name": document.name,
                    "type": document.doc_type,
                    "category": document.category,
                    "fileHash": document.file_hash,
                    "fileSize": document.file_size,
                    "fileType": document.file_type,
                    "createdAt": document.created_at.isoformat() + "Z",
                },
                "privacy": {
                    "rawFileStoredInDocWalletOnly": True,
                    "rawFileReturned": False,
                    "rawTextReturned": False,
                },
            }
        ), 201
