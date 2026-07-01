# Sync/Backfill da Master de Auditoria Comercial

ETL determinístico em Python que substitui o Apps Script "SYNC DE FECHAMENTOS v2"
(quebrado). Lê as planilhas de fechamento dos closers (`.xlsx` no Drive) e faz
**upsert por `Chave`** na master nativa, **in-place**.

## Garantias (spec §9)

O script **nunca**: deleta linha/aba, converte `.xlsx` em nativo, escreve as
colunas manuais, ou toca a formatação de linhas existentes. Escreve **dado, nunca
formatação** — linha existente preserva a cor; linha nova fica branca.

- Colunas **do sync** (sobrescritas a cada run): Closer, Nicho, Nome, Data, TAG,
  Tarefa, Observações, Chave, Atualizado em.
- Colunas **manuais** (preservadas, nunca escritas): Doc pessoal, Comprovante de
  residência, Carteira/Holerite, Documento complementar, Petição protocolada
  (ProJuris), **Status documentação**.

## Arquivos

| Arquivo | Papel |
|---|---|
| `sync_core.py` | Lógica determinística pura (repair de data, Chave, roteamento, plano de upsert). Sem dependências externas. |
| `sync_master.py` | I/O Google (Drive + Sheets) + CLI. |
| `test_sync_core.py` | Testes pytest (fixtures §8, Chave, colunas, skip, roteamento, idempotência). |
| `requirements.txt` | Dependências. |

## Instalação

```powershell
cd "C:\Users\Windows 11\auditoria-comercial-sync"
pip install -r requirements.txt
```

## Credencial (service account)

A SA precisa de **leitura nas fontes** (Drive) e **edição na master** (Sheets).
Compartilhe a master e as 5 fontes com o e-mail da SA. **A credencial nunca vai
para o vault, log ou chat.** O valor de `ALF_SYNC_SA_JSON` pode ser um **caminho
de arquivo** (dev local) OU o **conteúdo JSON** inteiro (Railway, que só tem env
string) — a mesma variável serve nos dois casos:

```powershell
$env:ALF_SYNC_SA_JSON = "C:\caminho\fora-do-vault\sa.json"   # caminho (local)
# ou passe --sa "C:\caminho\sa.json" em cada execução
# no Railway: cole o conteúdo do JSON inteiro na env var (não um caminho)
```

## Execução (2 passos — §13)

**1) Dry-run** (padrão; não escreve nada). Substitui a auditoria manual: o Raul
olha o resumo (X inserts, Y updates, Z datas reparadas, N sem-data) e libera.

```powershell
python sync_master.py
```

**2) Aplicar** de verdade, depois de conferir o dry-run:

```powershell
python sync_master.py --apply
```

### Filtros úteis

```powershell
python sync_master.py --mode backfill          # só Raul + Catarina (one-time)
python sync_master.py --mode ativo --apply     # só os 3 ativos (sync contínuo)
python sync_master.py --only "Raul Gabriel"    # uma fonte específica
```

Como o upsert é **idempotente** (dedup por `Chave`), re-rodar o backfill não
duplica. O sync contínuo roda só `--mode ativo` (exclui Raul/Catarina, backfill
one-time já aplicado).

## Deploy Railway (cron 2x/dia)

O cron roda o sync contínuo dos 3 closers ativos, com guard de segurança e
notificação por e-mail a cada execução.

**Start command:**

```
python sync_master.py --mode ativo --apply
```

**Schedule (cron do Railway, em UTC):**

```
0 16,22 * * *
```

= **13h e 19h BRT** (UTC−3). Duas passadas por dia.

**Variáveis de ambiente:**

| Var | Obrigatória | Descrição |
|---|---|---|
| `ALF_SYNC_SA_JSON` | sim | **Conteúdo JSON** da service account (não caminho, no Railway). |
| `ALF_SYNC_TG_TOKEN` | p/ notificação | Token do bot do Telegram (BotFather). |
| `ALF_SYNC_TG_CHAT` | p/ notificação | Chat ID de destino (pessoa, grupo ou canal). |
| `ALF_SYNC_MAX_INSERTS` | não | Teto do guard de insert (default **60**). |

**Guard de insert:** se um run `--apply` fosse inserir mais que `ALF_SYNC_MAX_INSERTS`
linhas, ele **aborta antes de escrever qualquer coisa** (nem update nem insert),
dispara a notificação `🚨 ... ABORTADO` e sai com código 1. Protege contra origem
corrompida/duplicada gerando avalanche de inserts.

**Notificação Telegram (só em `--apply`):** sucesso → `✅ Sync master OK` com o
resumo; abort → `🚨 ... ABORTADO`; exceção não tratada → `🚨 ... FALHOU` com o
traceback. Envio é best-effort (falha de rede não derruba o run) e corpos longos
são truncados em ~4000 chars. Dry-run **não** notifica.

Validar o canal Telegram no Railway sem rodar o sync:

```
python sync_master.py --test-notify
```

## Testes

```powershell
python -m pytest -q
```

## Notas

- **Cobertura esperada** após o run: ver spec §11. Confira no dry-run que os
  números batem antes do `--apply`.
- **Sem-data esperado**: 1 linha (Maiara, "Claudia Luana Leite de Souza",
  célula `#######`). Se o dry-run mostrar mais (ou menos), investigar antes de
  aplicar.
- **Artefato herdado** (spec §12): há 1 linha em junho/Beatriz com Nome ≠ Chave.
  O script **não conserta** linha existente — apenas registra. Correção manual à parte.
