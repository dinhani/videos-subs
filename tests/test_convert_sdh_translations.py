import importlib

import pytest

convert_sdh_translations = importlib.import_module("convert-sdh-translations")


@pytest.mark.parametrize(("sdh", "converted"), [
    ("(GRUNHE)", ""),
    ("[♪♪♪]", ""),
    ("(MULHER NO ALTO-FALANTE\nFALANDO)", ""),
    ("SCOTTY:", ""),
    ("CARL: O Shep disse\nque você estaria aqui", "O Shep disse\nque você estaria aqui"),
    ("ABIGAIL (abafada): Socorro!", "Socorro!"),
    ("(cantando junto):\n♪ Every time you drive by ♪", "♪ Every time you drive by ♪"),
    ("{\\an8}- [Joel] É ele?\n- [suspira]", "{\\an8}É ele?"),
    ("<i>[homem na TV] Por que tá me mostrando</i>", "<i>Por que tá me mostrando</i>"),
    ("<i>[Porta abre]\nQuem é?</i>", "<i>Quem é?</i>"),
    ("- [suspira]\n- Oi.", "Oi."),
    ("<i>- [Clementine] Um, dois, três.</i>\n<i>-[todos riem baixinho]</i>", "<i>Um, dois, três.</i>"),
    ("- Oi.\n- Tchau.", "- Oi.\n- Tchau."),
    ("Oi.\n- Tchau.", "Oi.\n- Tchau."),
    ("Eu disse: não.", "Eu disse: não."),
    ("A 7:30 da manhã.", "A 7:30 da manhã."),
    ("FRANK: ♪ I got the whole", "♪ I got the whole"),
    ("♪ Row, row, row your boat ♪", "♪ Row, row, row your boat ♪"),
])
def test_remove_sdh(sdh: str, converted: str) -> None:
    assert convert_sdh_translations.remove_sdh(sdh) == converted
