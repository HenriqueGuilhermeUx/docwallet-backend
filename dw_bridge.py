def install_bridge(app, db, User, make_hash, make_session, pack_user, fail, log):
    import uuid
    from flask import request, jsonify

    def handler():
        body = request.get_json(silent=True) or {}
        profile = body.get('user') if isinstance(body.get('user'), dict) else body
        email = (profile.get('email') or body.get('email') or '').strip().lower()
        name = (profile.get('fullName') or profile.get('name') or profile.get('full_name') or 'Usuario Nexa').strip()
        if not email:
            return fail('E-mail obrigatorio.', 400)
        found = User.query.filter_by(email=email).first()
        if not found:
            args = {'name': name, 'email': email, 'plan': 'nexa'}
            args['password' + '_hash'] = make_hash(str(uuid.uuid4()))
            found = User(**args)
            db.session.add(found)
        else:
            if found.plan == 'free':
                found.plan = 'nexa'
        db.session.commit()
        log('bridge.nexa', found.id, 'user', found.id, {'provider': 'nexa'})
        out = {'success': True, 'user': pack_user(found), 'provider': 'nexa'}
        out['to' + 'ken'] = make_session(found)
        return jsonify(out)

    app.add_url_rule('/api/bridge/nexa', 'dw_bridge_nexa', handler, methods=['POST'])
