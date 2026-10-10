import pytest

from aiengineer.nlp.intents import extract_entities, parse

CASES = {
    "Analiza este proyecto y dime qué problemas tiene": ["analyze"],
    "Corrige el balance de frecuencias y los problemas de enmascaramiento": ["mix_fix"],
    "Mezcla todas las pistas y utiliza los efectos necesarios": ["mix"],
    "Haz que el bajo y el kick tengan más pegada sin perder profundidad": ["kick_bass"],
    "Compón un drop progressive house con una melodía emotiva y memorable": ["compose"],
    "Rehaz la armonía de esta canción manteniendo el ritmo original": ["reharmonize"],
    "Crea un clímax más épico y una transición más emocional": ["climax"],
    "Masteriza el tema para un sistema de sonido de festival": ["master"],
    "Compara la mezcla con esta referencia y corrige las diferencias": ["compare_reference"],
    "Escucha el resultado, encuentra los problemas y sigue trabajando hasta mejorarlo": ["iterate"],
    "Conserva el ritmo de la melodía, cambia las notas y crea una armonía más nostálgica":
        ["reharmonize", "keep_rhythm_new_notes"],
    "Conserva el motivo principal, pero haz que la segunda frase sea una respuesta diferente": ["vary_answer"],
    "El bajo ha perdido cuerpo": ["feedback"],
    "La mezcla suena demasiado comprimida": ["feedback"],
}


@pytest.mark.parametrize("text,expected", list(CASES.items()))
def test_intents(text, expected):
    assert [i.name for i in parse(text)] == expected


def test_compound_order():
    names = [i.name for i in parse("Analiza el tema completo, identifica lo que necesita mejorar, corrige los problemas "
                                   "de mezcla, mejora la estructura musical, desarrolla la melodía, prepara el "
                                   "procesamiento y crea una versión masterizada")]
    assert names[0] == "analyze" and "compose" in names and names[-1] == "master"
    assert "mix_fix" in names or "mix" in names


def test_entities():
    e = extract_entities("Compón un tema de uplifting trance en Re mayor a 138 bpm, eufórico; masteriza a -9 LUFS y -1 dBTP")
    assert e["style"] == "uplifting_trance" and e["mood"] == "euforico" and e["key"] == "re mayor"
    assert e["bpm"] == 138 and e["lufs"] == -9 and e["ceiling"] == -1
    assert extract_entities("masteriza 'mi mezcla.wav' para club")["files"] == ["mi mezcla.wav"]


def test_questions_when_ambiguous():
    it = parse("Masteriza el tema")[0]
    assert it.questions and "WAV" in it.questions[0]
    assert not parse("Masteriza el tema", {"audio_file": "x.wav"})[0].questions
    assert parse("Haz algo bonito con la cafetera")[0].name == "unknown"
