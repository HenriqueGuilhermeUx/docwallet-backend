def install_nexoffice_upload_bridge(app, db, User, Document, fail, log):
    """Secure multipart upload bridge used only by trusted NexOffice backend calls.

    Raw bytes are persisted by DocWallet. NexOffice receives metadata/reference only.
    A workspace must already be linked to exactly one active DocWallet user.
    """
    import hashlib
    import hmac
    import os
    import uuid
    from pathlib import Path

    from flask import jsonify, request
    from sqlalchemy import text
    from werkzeug.utils import secure_filename

    enabled = os.environ.get("NEXOFFICE_BRIDGE_ENABLED", "true").lower() == "true"
    service_key = os.environ.get("NEXOFFICE_SERVICE_KEY", "").strip()
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

    def workspace_id_from_request():
        value = (request.headers.get("X-NexOffice-Workspace-ID") or "").strip()
        if not value:
            return None, "workspace_required"
        try:
            uuid.UUID(value)
            return value, None
        except Exception:
            return None, "invalid_workspace"

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
            return None, "workspace_not_connected"
        if len(rows) > 1:
            return None, "workspace_connection_ambiguous"
        user = db.session.get(User, rows[0]["user_id"])
        if not user:
            return None, "workspace_owner_missing"
        return user, None

    def sha256_file(path):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @app.post("/api/internal/nexoffice/documents/upload")
    def nexoffice_upload_document():
        if not enabled:
            return fail("NexOffice bridge desabilitado.", 503, {"code": "bridge_disabled"})
        if not service_key:
            return fail("NexOffice bridge não configurado.", 503, {"code": "service_key_not_configured"})
        supplied = supplied_service_key()
        if not supplied or not hmac.compare_digest(supplied, service_key):
            return fail("Credencial NexOffice inválida.", 401, {"code": "invalid_service_key"})

        workspace_id, workspace_error = workspace_id_from_request()
        if workspace_error:
            return fail("Workspace NexOffice ausente ou inválido.", 400, {"code": workspace_error})
        owner, owner_error = linked_user(workspace_id)
        if owner_error == "workspace_not_connected":
            return fail("Conecte a DocWallet a este workspace antes do upload.", 409, {"code": owner_error})
        if owner_error == "workspace_connection_ambiguous":
            return fail("Há mais de uma conta DocWallet vinculada a este workspace. Defina uma conta documental principal antes do upload.", 409, {"code": owner_error})
        if owner_error:
            return fail("A conta DocWallet vinculada não está disponível.", 409, {"code": owner_error})

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
