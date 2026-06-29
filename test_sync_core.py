"""
Testes determinísticos do sync_core (pytest).

Cobrem: repair de data (fixtures §8), geração de Chave, classificação de
colunas (manual preservada no update), skip de linha (§10), roteamento por
data, parse de bloco e idempotência do upsert (§9).
"""

from datetime import date, datetime

import pytest

import sync_core as c


# --------------------------------------------------------------------------- #
# §8 — Repair de data: fixtures obrigatórios.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw, block_year, expected", [
    ("09/042026", 2026, date(2026, 4, 9)),    # barra faltando
    ("14/012026", 2026, date(2026, 1, 14)),
    ("19/052026", 2026, date(2026, 5, 19)),
    ("25/5/0206", 2026, date(2026, 5, 25)),   # ano lixo -> ano do bloco
    ("14/1/2025", 2026, date(2026, 1, 14)),   # bloco Jan -> 2026
    ("9/3/2023",  2026, date(2026, 3, 9)),    # bloco Mar -> 2026
    ("20/05/2025", 2026, date(2026, 5, 20)),  # bloco Mai -> 2026
    ("6/10/2025", 2025, date(2025, 10, 6)),   # bloco Out -> 2025 (sem override)
])
def test_repair_date_fixtures(raw, block_year, expected):
    assert c.repair_date(raw, block_year) == expected


def test_repair_date_irrecuperavel():
    assert c.repair_date("#######", 2026) is None
    assert c.repair_date("", 2026) is None
    assert c.repair_date(None, 2026) is None


def test_repair_date_iso_e_datetime():
    assert c.repair_date("2026-04-09", 2026) == date(2026, 4, 9)
    assert c.repair_date(datetime(2026, 4, 9, 13, 0), 2026) == date(2026, 4, 9)
    # datetime com ano do bloco errado também é reconciliado
    assert c.repair_date(datetime(2025, 5, 20), 2026) == date(2026, 5, 20)


def test_analyze_date_status():
    assert c.analyze_date("09/04/2026", 2026)[1] == "parsed"
    assert c.analyze_date("09/042026", 2026)[1] == "reformatted"
    assert c.analyze_date("20/05/2025", 2026)[1] == "reconciled"
    assert c.analyze_date("#######", 2026) == (None, "sd")


# --------------------------------------------------------------------------- #
# §8 — parse de bloco (aba de origem).
# --------------------------------------------------------------------------- #
def test_parse_block_com_ano():
    assert c.parse_block("ABRIL - 2026") == (4, 2026)
    assert c.parse_block("MARÇO - 2026") == (3, 2026)
    assert c.parse_block("FEVEREIRO - 2026") == (2, 2026)


def test_parse_block_sem_ano_infere_janela():
    assert c.parse_block("FECHAMENTOS - OUTUBRO") == (10, 2025)   # Out -> 2025
    assert c.parse_block("FECHAMENTOS - JANEIRO") == (1, 2026)    # Jan -> 2026


def test_parse_block_template_ignorado():
    assert c.parse_block("Modelo de planilha") is None
    assert c.parse_block("FECHAMENTOS - (MÊS REFERÊNCIA)") is None


# --------------------------------------------------------------------------- #
# §7 — Chave.
# --------------------------------------------------------------------------- #
def test_make_key_exemplos_reais():
    assert (c.make_key("João Guilherme", "Jair Marcos Barbosa", date(2025, 10, 6))
            == "João Guilherme|jair marcos barbosa|2025-10-06")
    assert (c.make_key("Beatriz de Souza", "Silvestre Dias", date(2026, 2, 2))
            == "Beatriz de Souza|silvestre dias|2026-02-02")
    assert (c.make_key("Maiara Mendes", "Ana Paula da Silva", date(2026, 5, 15))
            == "Maiara Mendes|ana paula da silva|2026-05-15")


def test_make_key_normaliza_nome():
    assert (c.make_key("X", "  ANA   PAULA  da   Silva ", date(2026, 1, 1))
            == "X|ana paula da silva|2026-01-01")


def test_make_key_sem_data():
    assert c.make_key("Maiara Mendes", "Claudia Luana", None).endswith("|sd")


# --------------------------------------------------------------------------- #
# §6 — Observações.
# --------------------------------------------------------------------------- #
def test_build_observacoes():
    assert c.build_observacoes("a", "b") == "a | b"
    assert c.build_observacoes("  a  ", None) == "a"
    assert c.build_observacoes(None, " b ") == "b"
    assert c.build_observacoes("", "  ") == ""


# --------------------------------------------------------------------------- #
# §10 — Skip de linha.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name, skip", [
    ("", True),
    ("   ", True),
    (None, True),
    ("FECHAMENTOS - ABRIL", True),
    ("NOME", True),
    ("TOTAL:", True),
    ("Modelo de planilha", True),
    ("João da Silva", False),
    ("Ana Paula", False),
    # Subtítulos de bloco (mês + ano 20xx) -> pular.
    ("Março - 2026", True),
    ("MAIO-2026 FECHAMENTOS - 05/2026 - CATARINA", True),
    ("JANEIRO - 2026", True),
    # Clientes reais -> não pular.
    ("Claudia Luana Leite de Souza", False),   # sem ano; col4 corrompida vira sem-data
    ("José Maria dos Santos", False),
    ("Abril Santos", False),                    # mês sem ano não pula
])
def test_should_skip_row(name, skip):
    assert c.should_skip_row(name) is skip


# --------------------------------------------------------------------------- #
# §6 — transform_row: mapeamento posicional + roteamento por data.
# --------------------------------------------------------------------------- #
def _row(nome="", c4=None, c5=None, c6=None, c9=None, c10=None):
    cells = [nome, "", "", "", c4, c5, c6, "", "", c9, c10]
    return cells


def test_transform_row_mapeamento():
    cells = _row("Matheus Avelino", "16/04/2026", "Cliente Antigo",
                 "- já era nosso cliente", None, "Kaue: falta holerite")
    r = c.transform_row(cells, "Raul Gabriel", "Vícios de Construção", 4, 2026, "T")
    assert r["nome"] == "Matheus Avelino"
    assert r["data_iso"] == "2026-04-16"
    assert r["tag"] == "Cliente Antigo"
    assert r["tarefa"] == ""
    assert r["obs"] == "- já era nosso cliente | Kaue: falta holerite"
    assert r["nicho"] == "Vícios de Construção"
    assert r["target_tab"] == "ABRIL 2026"
    assert r["chave"] == "Raul Gabriel|matheus avelino|2026-04-16"


def test_transform_row_roteia_por_data_nao_pelo_bloco():
    # João: 13/1/2026 na aba MARÇO -> roteia para JANEIRO 2026.
    cells = _row("Fulano", "13/01/2026", "TAG")
    r = c.transform_row(cells, "João Guilherme", "Auxílio-Acidente", 3, 2026, "T")
    assert r["target_tab"] == "JANEIRO 2026"


def test_transform_row_sem_data_vai_pro_bloco():
    # Claudia Luana: data irrecuperável -> aba do bloco (junho).
    cells = _row("Claudia Luana", "#######", "TAG")
    r = c.transform_row(cells, "Maiara Mendes", "Vícios de Construção", 6, 2026, "T")
    assert r["data_iso"] == ""
    assert r["target_tab"] == "JUNHO 2026"
    assert r["chave"].endswith("|sd")


def test_transform_row_pula_lixo():
    assert c.transform_row(_row("TOTAL:"), "X", "Y", 4, 2026, "T") is None
    assert c.transform_row(_row(""), "X", "Y", 4, 2026, "T") is None


# --------------------------------------------------------------------------- #
# §5 — classificação de colunas: manual preservada no update, branca no insert.
# --------------------------------------------------------------------------- #
def test_sync_e_manual_particionam_o_cabecalho():
    assert sorted(c.SYNC_COLS + c.MANUAL_COLS) == list(range(len(c.HEADER)))
    assert set(c.SYNC_COLS).isdisjoint(c.MANUAL_COLS)


def _cols_from_a1(rng):
    # 'TAB'!A12:E12  -> {0,1,2,3,4}
    body = rng.split("!", 1)[1]
    def colnum(cell):
        letters = "".join(ch for ch in cell if ch.isalpha())
        n = 0
        for ch in letters:
            n = n * 26 + (ord(ch) - 64)
        return n - 1
    if ":" in body:
        a, b = body.split(":")
        return set(range(colnum(a), colnum(b) + 1))
    return {colnum(body)}


def test_update_so_toca_colunas_do_sync():
    row = dict(closer="C", nicho="N", nome="No", data_iso="2026-01-01",
               tag="T", tarefa="Ta", obs="O", chave="K", atualizado="U")
    touched = set()
    for rng, _ in c.update_segments("JANEIRO 2026", 12, row):
        touched |= _cols_from_a1(rng)
    assert touched == set(c.SYNC_COLS)
    assert touched.isdisjoint(c.MANUAL_COLS)


def test_insert_deixa_manuais_em_branco():
    row = dict(closer="C", nicho="N", nome="No", data_iso="2026-01-01",
               tag="T", tarefa="Ta", obs="O", chave="K", atualizado="U")
    vals = c.insert_row_values(row)
    assert len(vals) == len(c.HEADER)
    for i in c.MANUAL_COLS:
        assert vals[i] == ""
    assert vals[0] == "C" and vals[13] == "K"


# --------------------------------------------------------------------------- #
# §9 — upsert: insert vs update, idempotência, dedup interno.
# --------------------------------------------------------------------------- #
def _mkrow(chave):
    return dict(chave=chave, closer="C", nicho="N", nome="No",
                data_iso="2026-01-01", tag="", tarefa="", obs="",
                atualizado="U", target_tab="JANEIRO 2026")


def test_build_plan_insert_vs_update():
    rows = [_mkrow("a"), _mkrow("b"), _mkrow("c")]
    inserts, updates = c.build_plan(rows, existing_keys={"b"})
    assert {r["chave"] for r in inserts} == {"a", "c"}
    assert {r["chave"] for r in updates} == {"b"}


def test_build_plan_idempotente():
    # 1ª rodada: tudo insert. 2ª rodada (chaves já existem): tudo update, 0 insert.
    rows = [_mkrow("a"), _mkrow("b")]
    inserts1, _ = c.build_plan(rows, existing_keys=set())
    keys = {r["chave"] for r in inserts1}
    inserts2, updates2 = c.build_plan(rows, existing_keys=keys)
    assert inserts2 == []
    assert len(updates2) == 2


def test_build_plan_dedup_interno():
    rows = [_mkrow("dup"), _mkrow("dup")]
    inserts, updates = c.build_plan(rows, existing_keys=set())
    assert len(inserts) == 1
    assert updates == []
