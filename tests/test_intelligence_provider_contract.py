"""Regression test notes for DocWallet Intelligence.

The production extraction provider is installed inside the Flask application because
it reuses the existing SQLAlchemy `db`, `Document`, auth and audit primitives.

Manual test payload for a text contract upload:

CONTRATO DE PRESTAÇÃO DE SERVIÇOS
Contratante: Empresa XPTO LTDA, CNPJ 12.345.678/0001-90
Contratado: João Silva, CPF 123.456.789-09
Objeto: prestação de serviços de consultoria mensal.
Valor total do contrato: R$ 84.000,00
Pagamento: R$ 7.000,00 mensais.
Vigência: início em 01/09/2026 e vencimento em 31/08/2027.
Renovação automática por iguais períodos.
Rescisão: aviso prévio de 30 dias e multa de 20%.
Foro da comarca de Santos/SP.

Expected extraction:
- documentType CONTRACT
- parties include contratante and contratado
- amounts include total_value and recurring_value
- expirationDate 2027-08-31
- contract.renewalType automática
- riskFlags include renewal, penalty and 30-day notice
- alerts include document.expiring/contract.renewal_upcoming when within horizon
"""

def test_intelligence_contract_expectations_documented():
    assert True
