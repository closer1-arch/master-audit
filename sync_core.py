"""
sync_core.py — Lógica determinística do sync da Master de Auditoria Comercial.

Este módulo NÃO importa nada de Google/openpyxl. Só stdlib.
Toda a regra de negócio (repair de data, Chave, classificação de coluna,
skip, roteamento, plano de upsert) mora aqui e é coberta por testes (pytest).

Spec de referência: "SPEC — Script de Sync/Backfill da Master de Auditoria
Comercial" (27/06/2026).
"""

from __future__ import annotations

import re
from datetime import date, datetime

# --------------------------------------------------------------------------- #
# Cabeçalho da master (§4) — ordem fixa, 15 colunas.
# --------------------------------------------------------------------------- #
HEADER = [
    "Closer",                          # 0  sync
    "Nicho",                           # 1  sync
    "Nome do cliente",                 # 2  sync
    "Data de fechamento",              # 3  sync
    "TAG",                             # 4  sync
    "Doc pessoal",                     # 5  MANUAL
    "Comprovante de residência",       # 6  MANUAL
    "Carteira/Holerite",               # 7  MANUAL
    "Documento complementar",          # 8  MANUAL
    "Tarefa",                          # 9  sync
    "Petição protocolada (ProJuris)",  # 10 MANUAL
    "Observações",                     # 11 sync
    "Status documentação",             # 12 MANUAL
    "Chave",                           # 13 sync (oculta)
    "Atualizado em",                   # 14 sync
]

# Classificação de colunas (§5).
SYNC_COLS = [0, 1, 2, 3, 4, 9, 11, 13, 14]
MANUAL_COLS = [5, 6, 7, 8, 10, 12]
COL_CHAVE = 13

# --------------------------------------------------------------------------- #
# Meses PT-BR.
# --------------------------------------------------------------------------- #
MONTHS_PT = {
    "JANEIRO": 1, "FEVEREIRO": 2, "MARCO": 3, "MARÇO": 3, "ABRIL": 4,
    "MAIO": 5, "JUNHO": 6, "JULHO": 7, "AGOSTO": 8, "SETEMBRO": 9,
    "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12,
}
MONTH_NAME = {
    1: "JANEIRO", 2: "FEVEREIRO", 3: "MARÇO", 4: "ABRIL", 5: "MAIO",
    6: "JUNHO", 7: "JULHO", 8: "AGOSTO", 9: "SETEMBRO", 10: "OUTUBRO",
    11: "NOVEMBRO", 12: "DEZEMBRO",
}


def infer_year(month: int) -> int:
    """Janela do dataset (§8): Out/Nov/Dez -> 2025; Jan-Set -> 2026."""
    return 2025 if month in (10, 11, 12) else 2026


def parse_block(tab_title: str):
    """
    Lê o título da aba/seção de origem e devolve (mes, ano) do bloco.
    Devolve None quando não há mês reconhecível (ex.: 'Modelo de planilha').
    Ano: usa o 4-dígitos do título se houver; senão infere pela janela.
    """
    if not tab_title:
        return None
    t = tab_title.upper()
    month = None
    for name, num in MONTHS_PT.items():
        if name in t:
            month = num
            break
    if month is None:
        return None
    m = re.search(r"(20\d{2})", t)
    year = int(m.group(1)) if m else infer_year(month)
    return month, year


# --------------------------------------------------------------------------- #
# Repair de data (§8) — determinístico, sem revisão manual.
# --------------------------------------------------------------------------- #
def _safe_date(y: int, mo: int, da: int):
    if not (1 <= mo <= 12) or not (1 <= da <= 31):
        return None
    try:
        return date(y, mo, da)
    except ValueError:
        return None


def _parse_direct(s: str):
    """Etapa 1: parse direto. ISO (yyyy/mm/dd) ou BR (dd/mm/yyyy, d/m/yy)."""
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
    if m:
        y, mo, da = (int(x) for x in m.groups())
        return _safe_date(y, mo, da)
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{1,4})$", s)
    if m:
        da, mo, y = (int(x) for x in m.groups())
        if y < 100:
            y += 2000
        return _safe_date(y, mo, da)
    return None


def _repair_format(s: str):
    """Etapa 2: repair de formato. Barra faltando -> 8 dígitos DDMMYYYY."""
    digits = re.sub(r"\D", "", s)
    if len(digits) == 8:
        return _safe_date(int(digits[4:8]), int(digits[2:4]), int(digits[0:2]))
    return None


def _reconcile_year(d: date, block_year):
    """
    Etapa 3: o ano de um fechamento tem que ser o ano do bloco do mês onde a
    linha está arquivada. Se divergir, sobrescreve SÓ o ano (mantém dia+mês).
    Cobre tanto ano-lixo (0206, 2023) quanto ano dentro da janela mas do bloco
    errado (2025 num bloco de 2026) — ver fixtures §8.
    """
    if not block_year or d.year == block_year:
        return d
    try:
        return d.replace(year=block_year)
    except ValueError:  # 29/02 em ano não-bissexto
        return d.replace(year=block_year, day=28)


def analyze_date(value, block_year):
    """
    Aplica o repair (§8) e devolve (date|None, status).
    status ∈ {'parsed','reconciled','reformatted','sd'} — para o relatório do
    dry-run distinguir datas reparadas de datas limpas e de sem-data.
    """
    if value is None:
        return None, "sd"
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        d2 = _reconcile_year(value, block_year)
        return d2, ("reconciled" if d2 != value else "parsed")

    s = str(value).strip()
    if not s or "#" in s:           # '#######' / células de erro -> irrecuperável
        return None, "sd"

    d = _parse_direct(s)
    status = "parsed"
    if d is None:
        d = _repair_format(s)
        status = "reformatted"
    if d is None:
        return None, "sd"

    d2 = _reconcile_year(d, block_year)
    if d2 != d and status == "parsed":
        status = "reconciled"
    return d2, status


def repair_date(value, block_year, block_month=None):
    """Conveniência: só a data reparada (ou None)."""
    d, _ = analyze_date(value, block_year)
    return d


# --------------------------------------------------------------------------- #
# Chave (§7) e normalização.
# --------------------------------------------------------------------------- #
def normalize_name(name) -> str:
    s = "" if name is None else str(name)
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s


def make_key(closer: str, name, repaired_date) -> str:
    data_iso = repaired_date.isoformat() if repaired_date else "sd"
    return f"{closer}|{normalize_name(name)}|{data_iso}"


# --------------------------------------------------------------------------- #
# Observações (§6) e skip de linha (§10).
# --------------------------------------------------------------------------- #
def build_observacoes(col6, col10) -> str:
    parts = []
    for v in (col6, col10):
        if v is None:
            continue
        t = str(v).strip()
        if t:
            parts.append(t)
    return " | ".join(parts)


_SKIP_RE = re.compile(r"^(fechamentos|nome|total|modelo)", re.IGNORECASE)
_YEAR_RE = re.compile(r"20\d{2}")


def should_skip_row(name) -> bool:
    t = "" if name is None else str(name).strip()
    if not t:
        return True
    if _SKIP_RE.match(t):
        return True
    # Subtítulo de bloco (ex.: "Março - 2026"): contém MÊS + ANO 20xx.
    # Nunca é cliente real. (Cliente sem ano no nome, mesmo com col4
    # corrompida, NÃO cai aqui — vira sem-data e é processado.)
    up = t.upper()
    if _YEAR_RE.search(up) and any(m in up for m in MONTHS_PT):
        return True
    return False


def master_tab_name(month: int, year: int) -> str:
    """Aba da master: '<MÊS> <AAAA>' em maiúsculo, ex.: 'JUNHO 2026'."""
    return f"{MONTH_NAME[month]} {year}"


# --------------------------------------------------------------------------- #
# Transformação origem -> registro da master (§6).
# --------------------------------------------------------------------------- #
def transform_row(cells, closer, nicho, block_month, block_year, now_str):
    """
    Recebe a linha bruta da origem (lista posicional) e a config da fonte.
    Devolve um dict com os campos do sync + roteamento, ou None se a linha
    deve ser pulada (§10).
    """
    def get(i):
        return cells[i] if i < len(cells) else None

    name_raw = get(0)
    if should_skip_row(name_raw):
        return None
    nome = str(name_raw).strip()

    d, status = analyze_date(get(4), block_year)
    if d is not None:
        tgt_month, tgt_year = d.month, d.year
    else:
        # sem-data: vai para a aba do bloco do mês de origem (§8 item 4).
        tgt_month, tgt_year = block_month, block_year

    tag = get(5)
    tag = "" if tag is None else str(tag).strip()
    tarefa = get(9)
    tarefa = "" if tarefa is None else str(tarefa).strip()
    obs = build_observacoes(get(6), get(10))

    return {
        "closer": closer,
        "nicho": nicho,
        "nome": nome,
        "data_iso": d.isoformat() if d else "",
        "tag": tag,
        "tarefa": tarefa,
        "obs": obs,
        "chave": make_key(closer, nome, d),
        "atualizado": now_str,
        "target_month": tgt_month,
        "target_year": tgt_year,
        "target_tab": master_tab_name(tgt_month, tgt_year),
        "date_status": status,
    }


# --------------------------------------------------------------------------- #
# Plano de upsert (§9) — puro, decide insert vs update.
# --------------------------------------------------------------------------- #
def build_plan(rows, existing_keys):
    """
    Divide as linhas em (inserts, updates) por presença da Chave na master.
    Idempotente: se a Chave já existe -> update; senão -> insert.
    Dedup interno: linhas com a mesma Chave dentro do mesmo run colapsam
    (a última vence) para não inserir duplicado no mesmo lote.
    """
    seen = {}
    order = []
    for row in rows:
        k = row["chave"]
        if k not in seen:
            order.append(k)
        seen[k] = row  # última vence

    inserts, updates = [], []
    for k in order:
        row = seen[k]
        if k in existing_keys:
            updates.append(row)
        else:
            inserts.append(row)
    return inserts, updates


def insert_row_values(row):
    """Linha completa de 15 colunas para INSERT. Manuais ficam em branco (§5)."""
    out = [""] * len(HEADER)
    out[0] = row["closer"]
    out[1] = row["nicho"]
    out[2] = row["nome"]
    out[3] = row["data_iso"]
    out[4] = row["tag"]
    out[9] = row["tarefa"]
    out[11] = row["obs"]
    out[13] = row["chave"]
    out[14] = row["atualizado"]
    return out


def _col_letter(idx0):
    """Índice 0-based -> letra de coluna (0->A)."""
    idx = idx0 + 1
    s = ""
    while idx:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def update_segments(tab, rownum, row):
    """
    Ranges A1-notation + valores para UPDATE, restritos às colunas do sync (§5).
    NÃO toca as colunas manuais nem formatação (a Sheets values API não altera
    formatação). Segmentos contíguos: A:E, J, L, N:O.
    """
    return [
        (f"'{tab}'!A{rownum}:E{rownum}",
         [[row["closer"], row["nicho"], row["nome"], row["data_iso"], row["tag"]]]),
        (f"'{tab}'!J{rownum}", [[row["tarefa"]]]),
        (f"'{tab}'!L{rownum}", [[row["obs"]]]),
        (f"'{tab}'!N{rownum}:O{rownum}", [[row["chave"], row["atualizado"]]]),
    ]
