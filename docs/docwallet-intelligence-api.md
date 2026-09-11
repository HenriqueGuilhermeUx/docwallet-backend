# DocWallet Intelligence API

Base URL: `https://docwallet-backend.onrender.com`

Todas as rotas exigem `Authorization: Bearer <token>` exceto links públicos de assinatura já existentes.

## POST /api/documents/:id/analyze

Analisa um documento existente do usuário. Não altera o arquivo original.

Retorna `intelligence` com:

- `documentType`
- `title`
- `issuer`
- `documentNumber`
- `issueDate`
- `expirationDate`
- `summary`
- `confidence`
- `contract`
- `parties`
- `dates`
- `amounts`
- `obligations`
- `alerts`
- `versions`

## GET /api/documents/:id/intelligence

Lê a inteligência atual do documento.

Query opcional: `include_raw=true`.

## PATCH /api/documents/:id/intelligence

Permite revisão humana dos campos principais.

Exemplo:

```json
{
  "summary": "Resumo revisado pelo usuário.",
  "documentType": "CONTRACT",
  "expirationDate": "2027-08-31",
  "contract": {
    "renewalType": "automática"
  }
}
```

## GET /api/documents/:id/alerts

Lista alertas ativos do documento.

## POST /api/documents/:id/signature-request

Cria uma solicitação de assinatura a partir do documento e dos signatários detectados, reutilizando o motor DocWallet Sign.

## GET /api/documents/:id/audit-trail

Retorna eventos de auditoria seguros, sem conteúdo documental bruto.

## GET /api/documents/search?q=...

Busca em metadados, dados estruturados e texto indexado.

Exemplos:

- `Quais contratos vencem nos próximos 60 dias?`
- `Qual contrato tem multa de 20%?`
- `Quem ainda não assinou?`

## GET /api/contracts/upcoming-expirations?days=60

Retorna próximos vencimentos e alertas.

## GET /api/intelligence/dashboard

Métricas agregadas para o painel:

- documentos
- analisados
- contratos ativos
- vencendo em 30 dias
- assinaturas pendentes
- documentos com alerta

## GET /api/intelligence/metrics

Medição futura para monetização, apenas agregada.
