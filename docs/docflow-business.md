# DocFlow by DocWallet

**Tagline:** Transforme documentos em processos.

DocFlow by DocWallet é a camada SaaS B2B do ecossistema DocWallet. Ele não duplica o motor documental: usa AV Document Intelligence / DocWallet Intelligence para interpretar documentos e transformar o resultado em workflows configuráveis, aprovações, integrações e auditoria.

## Tese

Não vender OCR para documentos. Vender:

> Pare de digitar o que já está no papel.

E, no nível seguinte:

> Fotografe. O processo continua sozinho.

## Fluxo principal

1. Documento recebido por câmera mobile, upload web, email forwarding, API ou batch.
2. AV Document Intelligence extrai dados estruturados.
3. O usuário revisa dados extraídos.
4. DocFlow executa workflow configurável.
5. Gestor aprova ou rejeita.
6. Integração preparada para webhook, ERP, planilha, email, Slack, WhatsApp futuro, contabilidade ou storage.
7. Documento original fica arquivado no DocWallet com hash e trilha de auditoria.

## Templates iniciais

- Prestação de contas de viagem
- Reembolso de funcionário
- Compra de fornecedor
- Contas a pagar
- Conferência de notas
- Prestação de contas de obra
- Comprovante de entrega
- Ordens de serviço
- Contratos
- Onboarding documental de fornecedores
- Documentos de RH
- Inspeção de campo
- Auditoria documental

## Blocos no-code

- extract
- validate
- ask_user
- approve
- reject
- notify
- assign
- create_record
- webhook
- email
- export
- archive

## Endpoints

- GET /api/docflow/health
- GET /api/docflow/templates
- GET /api/docflow/dashboard
- GET /api/docflow/workflows
- POST /api/docflow/workflows
- POST /api/docflow/workflows/from-template
- GET /api/docflow/workflows/:id
- PATCH /api/docflow/workflows/:id
- GET /api/docflow/submissions
- POST /api/docflow/submissions
- GET /api/docflow/submissions/:id
- POST /api/docflow/submissions/:id/run
- POST /api/docflow/submissions/:id/approve
- POST /api/docflow/submissions/:id/reject
- GET /api/docflow/integrations
- POST /api/docflow/integrations
- GET /api/docflow/audit
- GET /api/docflow/metrics

## Tabelas

- docflow_tenant
- docflow_member
- docflow_workflow
- docflow_submission
- docflow_approval
- docflow_integration
- docflow_event
- docflow_metric

## Segurança

- Tenant isolation por owner/user.
- RBAC preparado em `docflow_member`.
- Integrações externas ficam desabilitadas por padrão.
- Secrets não ficam no frontend.
- Trilha de auditoria registra eventos sem conteúdo documental bruto.
- O documento original não é alterado.
- O hash do documento original é preservado quando o processo nasce de um documento DocWallet.

## Env vars

```env
DOCFLOW_ENABLED=true
DOCFLOW_DEFAULT_TENANT_NAME=DocFlow Workspace
DOCFLOW_EXTERNAL_INTEGRATIONS_ENABLED=false
```

## Limitações atuais

- Execução de webhook/ERP fica preparada, mas desabilitada por padrão.
- Email forwarding, WhatsApp e conectores reais devem entrar em etapa própria com secrets no backend.
- OCR de imagem escaneada depende da evolução do provider de Document Intelligence.
- RBAC avançado por equipe/departamento está modelado, mas a UI atual usa o usuário dono como workspace padrão.
