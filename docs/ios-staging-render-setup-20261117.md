# DocWallet Docs — staging iOS isolado no Render

Este documento **não provisiona** nem publica automaticamente um serviço. O arquivo `render-ios-qa.yaml` é um Blueprint opcional que deve ser selecionado manualmente. O `render.yaml` de produção não é alterado.

## Segurança da configuração

- Nome exclusivo: `docwallet-ios-qa-isolated`, branch `test/ios-private-download-contract-20261117`.
- Plano Free, `autoDeployTrigger: off`; não faz merge em `main`, não acessa Postgres de produção e não tem discos persistentes.
- Banco SQLite em `/tmp/docwallet-ios-qa-20261117.sqlite3` e uploads em `/tmp/docwallet-ios-qa-uploads`. **Dados são efêmeros e podem ser perdidos após reinicialização. Não usar dados reais.**
- `JWT_SECRET` e `DOCWALLET_IOS_STAGING_KEY` são gerados pelo Render (`generateValue: true`), não aparecem no Git.
- `staging_ios_entry.py` impede inicialização se faltar modo exclusivo, se apontar para banco/storage não temporário, se os segredos forem insuficientes ou se integrações externas proibidas estiverem ativas.
- Todas as rotas exceto `GET /api/health` exigem `X-DocWallet-Staging-Key`, **além** da autorização Bearer própria da API. A falta/erro da chave retorna 404.
- O site público normal **não** conhece essa chave; não incluí-la em `VITE_*`, browser, JS distribuído, APK, log ou capturas de tela. O teste inicial de staging deve ser feito por cliente HTTP controlado no lado servidor.

## Criação quando autorizada no painel

1. No workspace correto, escolher novo Blueprint, o repositório `HenriqueGuilhermeUx/docwallet-backend`, branch `test/ios-private-download-contract-20261117` e **caminho explícito** `render-ios-qa.yaml`; não usar `render.yaml`.
2. Inspecionar o diff/preview do Blueprint antes de aceitar: apenas **um novo** serviço `docwallet-ios-qa-isolated`, plano Free, sem banco PostgreSQL e sem modificações dos outros serviços.
3. Verificar que as duas variáveis de segredo serão geradas no Render e nunca copiadas para código. Não habilitar auto deploy.
4. Após a implantação, validar `GET /api/health` (status staging), um endpoint protegido sem header (404), com header de staging e sem Bearer (401), e finalmente a API autenticada com contas/arquivos **fictícios**. Guardar apenas status e IDs fictícios sanitizados.
5. Executar os casos DOC-01 a DOC-14 com backend e frontend de QA compatíveis. Não usar a instância para teste de clientes, dados pessoais ou documentos reais.

**Status em 17/11/2026:** Blueprint e guard criados em PR DRAFT; teste unitário isolado passou no CI. A criação do serviço via ferramenta foi bloqueada, portanto **o staging ainda não foi implantado**. Nenhuma mudança em produção.

Fonte do formato YAML: https://render.com/docs/blueprint-spec
