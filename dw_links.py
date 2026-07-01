def install_links(app, db, Document, auth_required, fail, log):
    import datetime as dt
    import secrets
    import uuid
    from pathlib import Path
    from flask import request, jsonify, send_file

    class SharedAccess(db.Model):
        __tablename__ = 'shared_access'
        id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
        code = db.Column(db.String(80), unique=True, nullable=False, index=True)
        user_id = db.Column(db.String(36), nullable=False, index=True)
        document_id = db.Column(db.String(36), nullable=False, index=True)
        expires_at = db.Column(db.DateTime, nullable=False)
        max_views = db.Column(db.Integer, nullable=True)
        view_count = db.Column(db.Integer, default=0, nullable=False)
        allow_download = db.Column(db.Boolean, default=True, nullable=False)
        is_revoked = db.Column(db.Boolean, default=False, nullable=False)
        created_at = db.Column(db.DateTime, default=dt.datetime.utcnow, nullable=False)

    with app.app_context():
        db.create_all()

    def pack(item):
        return {
            'id': item.id,
            'code': item.code,
            'document_id': item.document_id,
            'expires_at': item.expires_at.isoformat() + 'Z',
            'max_views': item.max_views,
            'view_count': item.view_count,
            'allow_download': item.allow_download,
            'is_revoked': item.is_revoked,
            'api_url': '/api/shared/' + item.code,
            'file_url': '/api/shared/' + item.code + '/open',
        }

    def find_live(code):
        item = SharedAccess.query.filter_by(code=code).first()
        if not item:
            return None, None, 'Link não encontrado.'
        if item.is_revoked:
            return None, None, 'Link revogado.'
        if item.expires_at < dt.datetime.utcnow():
            return None, None, 'Link expirado.'
        if item.max_views is not None and item.view_count >= item.max_views:
            return None, None, 'Limite de visualizações atingido.'
        doc = Document.query.filter_by(id=item.document_id).first()
        if not doc:
            return None, None, 'Documento não encontrado.'
        return item, doc, None

    @app.post('/api/shared')
    @auth_required
    def create_shared_access():
        body = request.get_json(silent=True) or {}
        doc_id = body.get('document_id') or body.get('documentId')
        hours = int(body.get('expires_hours') or 168)
        views = body.get('max_views')
        if not doc_id:
            return fail('Documento obrigatório.', 400)
        doc = Document.query.filter_by(id=doc_id, user_id=request.user.id).first()
        if not doc:
            return fail('Documento não encontrado.', 404)
        if hours < 1:
            hours = 1
        if hours > 720:
            hours = 720
        item = SharedAccess(
            code=secrets.token_hex(12),
            user_id=request.user.id,
            document_id=doc.id,
            expires_at=dt.datetime.utcnow() + dt.timedelta(hours=hours),
            max_views=int(views) if views not in (None, '', 0, '0') else None,
            allow_download=bool(body.get('allow_download', True)),
        )
        db.session.add(item)
        db.session.commit()
        log('shared.create', request.user.id, 'shared', item.id, {'document_id': doc.id})
        return jsonify({'success': True, 'share': pack(item)}), 201

    @app.get('/api/shared')
    @auth_required
    def list_shared_access():
        items = SharedAccess.query.filter_by(user_id=request.user.id).order_by(SharedAccess.created_at.desc()).all()
        return jsonify({'success': True, 'shares': [pack(i) for i in items]})

    @app.post('/api/shared/<code>/revoke')
    @auth_required
    def revoke_shared_access(code):
        item = SharedAccess.query.filter_by(code=code, user_id=request.user.id).first()
        if not item:
            return fail('Link não encontrado.', 404)
        item.is_revoked = True
        db.session.commit()
        log('shared.revoke', request.user.id, 'shared', item.id, {'code': code})
        return jsonify({'success': True, 'share': pack(item)})

    @app.get('/api/shared/<code>')
    def read_shared_access(code):
        item, doc, err = find_live(code)
        if err:
            return fail(err, 404)
        return jsonify({'success': True, 'share': pack(item), 'document': {
            'id': doc.id,
            'name': doc.name,
            'file_type': doc.file_type,
            'file_size': doc.file_size,
            'file_hash': doc.file_hash,
            'is_notarized': doc.is_notarized,
            'certificate_id': doc.certificate_id,
            'created_at': doc.created_at.isoformat() + 'Z',
        }})

    @app.get('/api/shared/<code>/open')
    def open_shared_access(code):
        item, doc, err = find_live(code)
        if err:
            return fail(err, 404)
        item.view_count += 1
        db.session.commit()
        path = Path(doc.file_path)
        if not path.exists():
            return fail('Arquivo não encontrado.', 404)
        force_download = request.args.get('download') == '1'
        if force_download and not item.allow_download:
            return fail('Download não permitido.', 403)
        return send_file(path, as_attachment=force_download, download_name=doc.original_filename, mimetype=doc.file_type)
