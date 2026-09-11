# DocWallet Sign Evidências Reforçadas

Esta é a solução própria do DocWallet para assinatura eletrônica enquanto a integração ICP-Brasil via provider externo não estiver ativa.

## Posicionamento correto

- Nome comercial: DocWallet Sign Evidências Reforçadas.
- Não é assinatura qualificada ICP-Brasil.
- Deve ser comunicada como assinatura eletrônica com trilha de evidências, aceite, hash e auditoria.
- A assinatura qualificada ICP-Brasil continua preparada para provider futuro, como Lacuna, BRy, Certisign, Valid ou Soluti.

## Evidências coletadas

Ao assinar pelo link público `/sign/:code`, o DocWallet registra:

- nome assinado;
- e-mail assinado;
- CPF opcional;
- telefone opcional;
- frase de confirmação: `EU ACEITO`;
- assinatura desenhada em canvas;
- hash SHA-256 da imagem da assinatura;
- data e hora UTC;
- IP;
- user-agent/navegador;
- dados do dispositivo, idioma, timezone, tela e viewport;
- geolocalização opcional, somente se o usuário permitir;
- texto de consentimento;
- hash SHA-256 original do contrato;
- hash final SHA-256 quando todas as partes assinam;
- eventos de auditoria.

## Segurança

- O documento original não é alterado.
- A assinatura desenhada é armazenada como evidência do aceite, não como certificado ICP.
- A geolocalização é opcional e depende de consentimento do dispositivo.
- A senha/chave/certificado digital do usuário nunca é solicitado pela solução própria.
- Para certificado digital ICP-Brasil, o DocWallet deve usar provider externo configurado.

## Fluxo

1. Dono do contrato cria uma solicitação em `/assinaturas`.
2. O DocWallet gera links únicos por signatário.
3. O signatário abre `/sign/:code`.
4. Informa nome/e-mail e, opcionalmente, CPF/telefone.
5. Desenha assinatura.
6. Digita `EU ACEITO`.
7. Confirma o aceite e autoriza o registro de evidências.
8. O backend grava as evidências e marca a parte como assinada.
9. Quando todas as partes assinam, o backend gera o hash final.
10. O dono baixa o pacote de evidências JSON pela área de assinaturas.

## Mensagem comercial segura

> Assine contratos eletronicamente com registro de aceite, assinatura desenhada, hash SHA-256, data/hora, IP, navegador, dispositivo e trilha de auditoria.

## Mensagem jurídica segura

> O DocWallet Sign Evidências Reforçadas oferece apoio probatório e trilha de evidências digitais. Não substitui assinatura qualificada ICP-Brasil, salvo quando a assinatura for concluída por provider ICP-Brasil habilitado.
