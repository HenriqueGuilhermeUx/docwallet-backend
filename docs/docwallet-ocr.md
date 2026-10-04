# DocWallet 5.0 — OCR com PaddleOCR

## Objetivo

Adicionar leitura de documentos escaneados e fotos ao pipeline já existente do
DocWallet Intelligence sem substituir a extração nativa de PDFs.

Fluxo:

```
PDF com texto -> pypdf --------------------┐
PDF escaneado -> PaddleOCR ----------------+-> DocWallet Intelligence
JPG/PNG/foto -> PaddleOCR -----------------┘
                                             -> tipo / partes / datas / valores
                                             -> obrigações / riscos / alertas
                                             -> DocFlow / assinatura / comprovação
```

## Estratégia

A extração é **native-first**. O OCR só é usado quando:

- o arquivo é uma imagem; ou
- o PDF tem menos texto embutido que `DOCUMENT_OCR_MIN_NATIVE_CHARS`.

Isso evita gastar CPU/memória com PDFs que já possuem camada de texto.

## Privacidade

A implementação V1 usa **PaddleOCR local**. Nenhum documento é enviado
automaticamente a uma API externa. O carregamento do PaddleOCR é lazy e o
backend continua funcionando mesmo quando o runtime opcional não está
instalado.

## Ativação

O core continua em `requirements.txt`.

Para um ambiente dedicado de OCR:

```bash
pip install -r requirements-ocr.txt
```

Variáveis:

```
DOCUMENT_OCR_ENABLED=true
DOCUMENT_OCR_PROVIDER=paddle_local
DOCUMENT_OCR_LANGUAGE=pt
DOCUMENT_OCR_VERSION=PP-OCRv5
DOCUMENT_OCR_DETECTION_MODEL=PP-OCRv5_mobile_det
DOCUMENT_OCR_RECOGNITION_MODEL=latin_PP-OCRv5_mobile_rec
DOCUMENT_OCR_DEVICE=cpu
DOCUMENT_OCR_MIN_NATIVE_CHARS=120
DOCUMENT_OCR_MAX_NATIVE_PAGES=40
DOCUMENT_OCR_MAX_PDF_PAGES=40
DOCUMENT_OCR_USE_DOC_ORIENTATION=false
DOCUMENT_OCR_USE_DOC_UNWARPING=false
DOCUMENT_OCR_USE_TEXTLINE_ORIENTATION=false
DOCUMENT_OCR_FAIL_OPEN=true
```

## Metadados de Intelligence

Cada análise registra, sem duplicar o conteúdo bruto em logs:

- `textExtractionSource`
- `ocrProvider`
- `ocrUsed`
- `nativeTextChars`
- `ocrTextChars`
- `ocrPages`
- `ocrConfidence`
- `ocrStatus`
- `ocrReason`

## Segurança operacional

`DOCUMENT_OCR_FAIL_OPEN=true` preserva o comportamento anterior: se o runtime
OCR estiver indisponível, PDFs com texto nativo continuam sendo processados e
o erro fica apenas nos metadados da análise.

PDFs acima do limite configurado não entram em OCR automaticamente, evitando
processamento descontrolado.

## Produção

Não alterar o serviço principal para `requirements-ocr.txt` sem medir memória
e latência. PaddlePaddle é pesado e o backend atual usa múltiplos workers.
A ativação recomendada é validar primeiro em serviço/worker isolado e só então
habilitar a flag do ambiente de produção.

## Perfil de memória

Em instâncias pequenas, usar os modelos mobile. O detector `PP-OCRv5_mobile_det` e o reconhecedor `latin_PP-OCRv5_mobile_rec` são adequados para português e reduzem bastante o footprint em relação aos modelos médios PP-OCRv6. O perfil de homologação usa também orientação desabilitada para não carregar um terceiro modelo.
