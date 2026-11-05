from pathlib import Path

import pytest

from shared.data_lists import DATA_LISTS, DataList, DataListFormat


def data_list(tmp_path: Path, format: DataListFormat, *lines: str) -> DataList:
    file = tmp_path / "list.txt"
    file.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return DataList(file=file, format=format)


@pytest.mark.parametrize("data_list_real", DATA_LISTS, ids=lambda data_list: data_list.name)
def test_real_lists_parse(data_list_real: DataList) -> None:
    for item in data_list_real.parse():
        data_list_real.format.ensure_valid(" = ".join(item))


@pytest.mark.parametrize(("format", "item"), [
    (DataListFormat.WORD, "two words"),
    (DataListFormat.EXPRESSION, "word"),
    (DataListFormat.PAIR, "grunts"),
    (DataListFormat.PAIR, "grunts = "),
    (DataListFormat.PAIR, "grunts =  grunhe"),
    (DataListFormat.TRIPLE, "maybe = talvez"),
    (DataListFormat.TRIPLE, "maybe = tal ve = talvez"),
    (DataListFormat.TRIPLE, "maybe = casa = talvez"),
    (DataListFormat.EXPRESSION_TRIPLE, "hold your fire = não atirem"),
])
def test_ensure_valid_refuses(format: DataListFormat, item: str) -> None:
    with pytest.raises(ValueError):
        format.ensure_valid(item)


def test_add_sorts_without_accents_and_lowercases(tmp_path: Path) -> None:
    words = data_list(tmp_path, DataListFormat.WORD, "abelha", "casa")

    added = words.add(["Ébano", "banana", "casa"])

    assert added == {"ébano": True, "banana": True, "casa": False}
    assert words.file.read_text(encoding="utf-8").splitlines() == ["abelha", "banana", "casa", "ébano"]


def test_add_sorts_pairs_by_key(tmp_path: Path) -> None:
    sounds = data_list(tmp_path, DataListFormat.PAIR, "grunts = grunhe", "sighs = suspira")

    sounds.add(["gasps = arfa"])

    assert sounds.file.read_text(encoding="utf-8").splitlines() == ["gasps = arfa", "grunts = grunhe", "sighs = suspira"]


def test_add_refuses_key_listed_with_another_value(tmp_path: Path) -> None:
    sounds = data_list(tmp_path, DataListFormat.PAIR, "grunts = grunhe")

    with pytest.raises(ValueError):
        sounds.add(["grunts = grunhido"])
    assert sounds.file.read_text(encoding="utf-8").splitlines() == ["grunts = grunhe"]


def test_parse_refuses_line_of_another_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        data_list(tmp_path, DataListFormat.TRIPLE, "maybe = talvez").parse()
