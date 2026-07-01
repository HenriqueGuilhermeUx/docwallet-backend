def install_links(app, db, Document, auth_required, fail, log):
    import datetime as dt
    import secrets
    import uuid
    from flask import request, jsonify

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
