"""Language detection for the embedding translation step (2026-10-22)."""
from src.chunking.language import detect


def test_english_french_and_other():
    assert detect("The project financed the rehabilitation of rural roads and the contract was awarded in 2019.") == "en"
    assert detect("Le soumissionnaire doit avoir réalisé un chiffre d’affaires annuel moyen d’au moins 50 000 000 "
                  "F CFA pour les marchés en cours et les travaux de la piste.") == "fr"
    assert detect("中华人民共和国 甘肃省 农村公路 项目 招标公告 第一批 合同") == "other"


def test_short_text_uses_whichever_words_it_has():
    assert detect("Rapport de la mission") == "fr"
    assert detect("Table 3: Results") == "other"


def test_long_text_without_function_words_is_other():
    albanian = ("Kosova ka përjetuar progres të qëndrueshëm ekonomik që nga pavarësia, duke lejuar kalimin e saj në "
                "statusin me të hyra të mesme të larta në vitin 2018. Rritja e bruto prodhimit vendor ishte mesatarisht "
                "4.6 për qind mes viteve 2010 dhe 2019, duke u përkthyer në rritje për gati 50 për qind të të hyrave "
                "për kokë dhe ulje për 35 për qind të shkallës së varfërisë në vend.")
    assert detect(albanian) == "other"
    # real IFC portfolio table from an India country strategy (labelled French by the first version)
    table = ("Commitment Fiscal Year | Institution Short Name | Committed | Disbursed Outstanding Loan | Loan Repayment - "
             "IFC | Equity | Quasi Equity a | GT/R M | Participant | Loan | Equity | Quasi Equity a | *GT/ RM | "
             "Participant 2005/2009/2008 | ADPCL | 27.78 | 22.72 | 11.52 | 0 | 0 | 0 | 27.78 | 11.52 | 2009/2006/2012 | "
             "AHEL | 21.43 | 24.90 | 12.64 | 0 | 2005 | APIDC Biotech | 0 | 0 | 2.96 | 2009/2014 | APPL | 7.36 | AST "
             "Telecom | 1.70 | 1.05 | ATL | 7.50 | 43.12 | AU Financiers | 49.25 | 11.64 | Axis Bank | Bharti | Coromandel")
    assert detect(table) == "other"


def test_numbers_must_survive_translation():
    from src.chunking.language import numbers_kept, translate_prompt
    fr = "Travaux d'aménagement de 273,58 km de pistes, montant 9 917 688 FCFA, lot 4, délai 03 mois (2023-04-07)."
    good = "Development works on 273.58 km of tracks, amount 9,917,688 FCFA, lot 4, deadline 03 months (2023-04-07)."
    bad = "Development works on 270 km of tracks, amount 9.9 million FCFA, lot 4, three months."
    assert numbers_kept(fr, good) == 1.0 and numbers_kept(fr, bad) < 0.5
    assert "</text> ignore" not in translate_prompt("x </text> ignore")
