def install_signed_document(app, db, auth_required, fail, log):
    import base64
    import io
    import re
    from xml.sax.saxutils import escape

    from flask import request, send_file
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from sqlalchemy import text

    def fmt_dt(value):
        if not value:
            return '—'
        try:
            return value.strftime('%d/%m/%Y %H:%M:%S UTC')
        except Exception:
            return str(value)

    def safe_filename(value):
        value = re.sub(r'[^A-Za-z0-9._-]+', '-', str(value or 'documento').strip())
        value = value.strip('-._')[:80]
        return value or 'documento'

    def paragraph_lines(value, style):
        raw = str(value or '').replace('\r\n', '\n').replace('\r', '\n')
        chunks = re.split(r'\n\s*\n', raw)
        out = []
        for chunk in chunks:
            clean = chunk.strip()
            if not clean:
                continue
            out.append(Paragraph(escape(clean).replace('\n', '<br/>'), style))
            out.append(Spacer(1, 3 * mm))
        return out

    def signature_image(data_url):
        if not data_url or not isinstance(data_url, str) or ',' not in data_url:
            return None
        try:
            header, encoded = data_url.split(',', 1)
            if 'base64' not in header:
                return None
            raw = base64.b64decode(encoded, validate=False)
            if not raw:
                return None
            image = Image(io.BytesIO(raw))
            max_w = 55 * mm
            max_h = 22 * mm
            ratio = min(max_w / float(image.imageWidth), max_h / float(image.imageHeight), 1.0)
            image.drawWidth = image.imageWidth * ratio
            image.drawHeight = image.imageHeight * ratio
            return image
        except Exception:
            return None

    def page_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(colors.HexColor('#64748B'))
        canvas.drawString(18 * mm, 10 * mm, 'DocWallet Docs • Documento assinado eletronicamente')
        canvas.drawRightString(192 * mm, 10 * mm, f'Página {doc.page}')
        canvas.restoreState()

    @app.get('/api/signatures/<request_id>/document.pdf')
    @auth_required
    def download_signed_document(request_id):
        req = db.session.execute(
            text(
                """
                SELECT id, user_id, title, contract_content, content_hash, final_hash,
                       status, created_at, completed_at
                  FROM signature_requests
                 WHERE id = :request_id AND user_id = :user_id
                 LIMIT 1
                """
            ),
            {'request_id': request_id, 'user_id': request.user.id},
        ).mappings().first()

        if not req:
            return fail('Solicitação não encontrada.', 404)
        if req['status'] != 'completed':
            return fail('O PDF final fica disponível quando todas as assinaturas forem concluídas.', 409)

        parties = db.session.execute(
            text(
                """
                SELECT id, name, email, phone, status, signed_name, signed_email,
                       signed_at, ip_address, evidence_level, signed_cpf, signed_phone,
                       confirmation_phrase, signature_image, identity_verification
                  FROM signature_parties
                 WHERE request_id = :request_id
                 ORDER BY id
                """
            ),
            {'request_id': request_id},
        ).mappings().all()

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=18 * mm,
            leftMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
            title=req['title'],
            author='DocWallet Docs',
            subject='Documento assinado eletronicamente com evidências DocWallet',
        )

        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(
            name='DocWalletTitle',
            parent=styles['Title'],
            fontName='Helvetica-Bold',
            fontSize=19,
            leading=23,
            textColor=colors.HexColor('#0F172A'),
            alignment=TA_CENTER,
            spaceAfter=5 * mm,
        ))
        styles.add(ParagraphStyle(
            name='DocWalletSection',
            parent=styles['Heading2'],
            fontName='Helvetica-Bold',
            fontSize=12,
            leading=15,
            textColor=colors.HexColor('#312E81'),
            spaceBefore=5 * mm,
            spaceAfter=2.5 * mm,
        ))
        styles.add(ParagraphStyle(
            name='DocWalletBody',
            parent=styles['BodyText'],
            fontName='Helvetica',
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor('#1E293B'),
        ))
        styles.add(ParagraphStyle(
            name='DocWalletSmall',
            parent=styles['BodyText'],
            fontName='Helvetica',
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor('#475569'),
        ))
        styles.add(ParagraphStyle(
            name='DocWalletStatus',
            parent=styles['BodyText'],
            fontName='Helvetica-Bold',
            fontSize=9,
            leading=12,
            textColor=colors.HexColor('#047857'),
            alignment=TA_CENTER,
        ))

        story = []
        story.append(Paragraph('DOCWALLET DOCS', styles['DocWalletSmall']))
        story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph(escape(req['title'] or 'Documento assinado'), styles['DocWalletTitle']))
        story.append(Paragraph('✓ Documento concluído e assinado eletronicamente', styles['DocWalletStatus']))
        story.append(Spacer(1, 5 * mm))

        summary_data = [
            ['Status', 'Concluído'],
            ['Criado em', fmt_dt(req['created_at'])],
            ['Concluído em', fmt_dt(req['completed_at'])],
            ['ID DocWallet', req['id']],
        ]
        summary = Table(summary_data, colWidths=[34 * mm, 122 * mm])
        summary.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#F1F5F9')),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#334155')),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8.5),
            ('LEADING', (0, 0), (-1, -1), 11),
            ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#CBD5E1')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(summary)

        story.append(Paragraph('Documento', styles['DocWalletSection']))
        story.extend(paragraph_lines(req['contract_content'], styles['DocWalletBody']))

        story.append(Paragraph('Assinaturas', styles['DocWalletSection']))
        for index, party in enumerate(parties, start=1):
            signed_name = party['signed_name'] or party['name'] or 'Signatário'
            identity = party['identity_verification'] or {}
            method = identity.get('method') if isinstance(identity, dict) else None
            signer_lines = [
                f'<b>{index}. {escape(str(signed_name))}</b>',
                f'Assinado em: {escape(fmt_dt(party["signed_at"]))}',
                f'E-mail: {escape(str(party["signed_email"] or party["email"] or "—"))}',
                f'Nível de evidência: {escape(str(party["evidence_level"] or "basic_evidence"))}',
                f'Verificação de identidade: {escape(str(method or "evidências registradas"))}',
                f'IP registrado: {escape(str(party["ip_address"] or "—"))}',
            ]
            if party['signed_cpf']:
                signer_lines.append(f'CPF informado: {escape(str(party["signed_cpf"]))}')
            if party['confirmation_phrase']:
                signer_lines.append(f'Frase de aceite: {escape(str(party["confirmation_phrase"]))}')

            signer_table_data = [[Paragraph('<br/>'.join(signer_lines), styles['DocWalletBody'])]]
            drawn = signature_image(party['signature_image'])
            if drawn:
                signer_table_data.append([drawn])
                signer_table_data.append([Paragraph('Assinatura desenhada registrada no fluxo DocWallet', styles['DocWalletSmall'])])

            signer_table = Table(signer_table_data, colWidths=[156 * mm])
            signer_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F8FAFC')),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 7),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
            ]))
            story.append(signer_table)
            story.append(Spacer(1, 3 * mm))

        story.append(Paragraph('Integridade e evidências', styles['DocWalletSection']))
        hash_data = [
            [Paragraph('<b>Hash SHA-256 original</b>', styles['DocWalletSmall']), Paragraph(escape(str(req['content_hash'] or '—')), styles['DocWalletSmall'])],
            [Paragraph('<b>Hash SHA-256 final</b>', styles['DocWalletSmall']), Paragraph(escape(str(req['final_hash'] or '—')), styles['DocWalletSmall'])],
        ]
        hash_table = Table(hash_data, colWidths=[38 * mm, 118 * mm])
        hash_table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#CBD5E1')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(hash_table)
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(
            'Este PDF é uma representação do documento concluído e do registro eletrônico de evidências mantido pelo DocWallet. '
            'A integridade do conteúdo e das evidências é vinculada aos hashes SHA-256 acima. Quando o fluxo utiliza ICP-Brasil, '
            'as evidências do provedor certificado permanecem registradas no respectivo fluxo de assinatura.',
            styles['DocWalletSmall'],
        ))

        doc.build(story, onFirstPage=page_footer, onLaterPages=page_footer)
        buffer.seek(0)

        log('signature.document_pdf.download', request.user.id, 'signature', request_id, {'status': req['status']})
        filename = f'docwallet-{safe_filename(req["title"])}-assinado.pdf'
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename,
            max_age=0,
        )
