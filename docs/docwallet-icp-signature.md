# DocWallet Sign ICP-Brasil

## Objetivo

Adicionar ao DocWallet Sign um modo de assinatura qualificada com certificado digital ICP-Brasil, sem substituir a assinatura eletrônica atual com evidências digitais.

## Modos de assinatura

1. `docwallet_evidence`
   - Link único por signatário.
   - Aceite eletrônico.
   - Nome, e-mail, data/hora, IP, user-agent.
   - Hash SHA-256 do conteúdo e hash final com evidências.

2. `icp_brasil`
   - Assinatura qualificada quando realizada com certificado digital ICP-Brasil por provider configurado.
   - Deve gerar documento assinado em padrão compatível, preferencialmente PAdES para PDF.
   - Deve preservar relatório de validação, certificado, emissor, serial, status e arquivo assinado.

## Princípios de segurança

- O DocWallet não deve armazenar senha de certificado digital.
- O DocWallet não deve armazenar chave privada de A1.
- A3/token/cartão deve ser tratado por componente/provider seguro.
- Certificado em nuvem deve ser tratado por provider externo com autenticação forte.
- Callback de provider exige segredo em backend.
- Frontend não contém secrets.

## Providers previstos

- `pending_provider`: arquitetura preparada, sem assinatura qualificada ativa.
- `lacuna`: futuro adapter.
- `bry`: futuro adapter.
- `valid`: futuro adapter.
- `certisign`: futuro adapter.
- `soluti`: futuro adapter.
- Outro PSC/provedor ICP-Brasil contratado.

## Variáveis de ambiente

```env
ICP_SIGNATURE_ENABLED=false
ICP_SIGNATURE_PROVIDER=pending_provider
ICP_SIGNATURE_MODE=external_provider
ICP_SIGNATURE_PROVIDER_BASE_URL=
ICP_SIGNATURE_CALLBACK_SECRET=
DOCWALLET_PUBLIC_URL=https://docwallet.netlify.app
```

Só habilitar `ICP_SIGNATURE_ENABLED=true` quando houver provider real configurado e testado.

## Endpoints

```txt
GET  /api/icp-signature/config
GET  /api/signatures/:requestId/icp/sessions
POST /api/signatures/:requestId/icp/prepare
POST /api/sign/:code/icp/start
POST /api/icp-signature/provider/callback
```

## UI

Na página pública de assinatura `/sign/:code`, o signatário vê dois caminhos:

- Assinar eletronicamente com evidências.
- Assinar com certificado digital ICP-Brasil.

Enquanto não houver provider configurado, o segundo caminho aparece como preparação segura e informa que a assinatura qualificada depende de provider ICP-Brasil ativo.

## Linguagem comercial correta

Usar:

> Assinatura eletrônica com evidências digitais, hash e trilha de auditoria. Para casos que exigem assinatura qualificada, o DocWallet está preparado para integrar assinatura com certificado digital ICP-Brasil.

Não usar antes do provider ativo:

> Assinatura ICP-Brasil garantida.
> Assinatura qualificada ativa.
> Substitui cartório.
