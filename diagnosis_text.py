"""Deterministic diagnosis spelling, display, and learning identities."""

import re
import unicodedata
from functools import lru_cache


NORMALIZATION_VERSION = 1
BUILTIN_DIAGNOSES = (
    "CEFALEA",
    "CEFALEA MIGRAÑOSA",
    "CEFALEA TENSIONAL",
    "CEFALEA INTENSA",
    "CEFALEA DE ORIGEN A INVESTIGAR",
    "ABORTO COMPLETO",
    "ABORTO INCOMPLETO",
    "AMENAZA DE ABORTO",
    "CRISIS DE SIBILANCIAS",
    "CRISIS DE BRONCOESPASMO",
    "CRISIS HIPERTENSIVA",
    "CRISIS HIPERTENSIVA TIPO URGENCIA",
    "CRISIS HIPERTENSIVA TIPO EMERGENCIA",
    "ARTRALGIA",
    "ARTROSIS",
    "TRAUMA",
    "ESGUINCE",
    "FRACTURA",
    "CONTUSIÓN",
    "LUXACIÓN",
    "AMIGDALITIS",
    "AMIGDALITIS AGUDA",
    "AMIGDALITIS AGUDA CON EXUDADO",
)

WORD_CORRECTIONS = {
    "IMCOMPLETO": "INCOMPLETO",
    "HIPERTENCIVA": "HIPERTENSIVA",
    "HIPERTENTIVA": "HIPERTENSIVA",
    "HIPERTECIVA": "HIPERTENSIVA",
    "HIPERTENCTIVA": "HIPERTENSIVA",
    "URGERCIA": "URGENCIA",
    "SIBILANCIA": "SIBILANCIAS",
    "BRONCOESPASM": "BRONCOESPASMO",
    "BRONCOESPAMOS": "BRONCOESPASMO",
    "BRONCOESPASMAS": "BRONCOESPASMO",
    "ALTRALGIA": "ARTRALGIA",
    "ALTRAGIA": "ARTRALGIA",
    "ARTRALGIAS": "ARTRALGIA",
    "AMIGADALITIS": "AMIGDALITIS",
    "AMIGDALITS": "AMIGDALITIS",
    "EXPUDADO": "EXUDADO",
    "CONTUSION": "CONTUSIÓN",
    "LUXACION": "LUXACIÓN",
}

ANATOMICAL_WORDS = frozenset(
    "BRAZO BRAZOS ANTEBRAZO ANTEBRAZOS MANO MANOS MUÑECA MUÑECAS CODO CODOS "
    "HOMBRO HOMBROS DEDO DEDOS PIERNA PIERNAS MUSLO MUSLOS RODILLA RODILLAS "
    "TOBILLO TOBILLOS PIE PIES CADERA CADERAS EXTREMIDAD EXTREMIDADES "
    "MIEMBRO MIEMBROS TORAX TÓRAX CUELLO ESPALDA LUMBAR COLUMNA "
    "TIBIA PERONÉ PERONE FÉMUR FEMUR HÚMERO HUMERO RADIO CÚBITO CUBITO "
    "FALANGE FALANGES METACARPO METATARSO TALÓN TALON PANTORRILLA".split()
)
LOCATION_WORDS = ANATOMICAL_WORDS | frozenset(
    "IZQUIERDO IZQUIERDA IZQUIERDOS IZQUIERDAS DERECHO DERECHA DERECHOS DERECHAS "
    "AMBOS AMBAS BILATERAL BILATERALES SUPERIOR SUPERIORES INFERIOR INFERIORES "
    "DE DEL EN EL LA LOS LAS Y A NIVEL".split()
)
LOCATION_TYPOS = {"IZUIRDO": "IZQUIERDO", "IZURDO": "IZQUIERDO"}
MUSCULOSKELETAL_ROOTS = frozenset(
    {
        "ARTRALGIA",
        "ARTROSIS",
        "TRAUMA",
        "TRAUMATISMO",
        "ESGUINCE",
        "FRACTURA",
        "CONTUSIÓN",
        "CONTUSION",
        "LUXACIÓN",
        "LUXACION",
    }
)


def _plain_key(value):
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


BUILTIN_LABELS = {_plain_key(name): name for name in BUILTIN_DIAGNOSES}


def canonical_diagnosis(value, *, trusted_labels=()):
    """Correct known spelling without inferring a clinical subtype or location."""
    text = re.sub(r"\s+", " ", str(value or "").upper()).strip()
    text = _expand_investigation(text)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)
    text = re.sub(r"\bBRONCO\s+ESPAMAS?\b", "BRONCOESPASMO", text)
    vocabulary = _word_vocabulary(tuple(trusted_labels))
    words = [
        _trusted_spelling(WORD_CORRECTIONS.get(word, word), vocabulary)
        for word in text.split()
    ]
    if words and words[0] in MUSCULOSKELETAL_ROOTS:
        words = [LOCATION_TYPOS.get(word, word) for word in words]
    text = " ".join(words).strip(" ,;:!?")
    text = re.sub(r"\bCRISIS HTA\b", "CRISIS HIPERTENSIVA", text)
    text = re.sub(r"\bAMIGDALITIS AGUDO\b", "AMIGDALITIS AGUDA", text)
    return BUILTIN_LABELS.get(_plain_key(text), text)


@lru_cache(maxsize=16)
def _word_vocabulary(labels):
    result = {}
    for label in (*BUILTIN_DIAGNOSES, *labels):
        for word in str(label).upper().split():
            result.setdefault(_plain_key(word), word)
    return result


def _trusted_spelling(word, vocabulary):
    key = _plain_key(word)
    if key in vocabulary:
        return vocabulary[key]
    if len(key) < 5:
        return word
    candidates = _spelling_variants(key).intersection(vocabulary)
    return vocabulary[next(iter(candidates))] if len(candidates) == 1 else word


def _spelling_variants(word):
    collapsed = {
        word[:index] + word[index + 1 :]
        for index in range(1, len(word))
        if word[index] == word[index - 1]
    }
    transposed = {
        word[:index] + word[index + 1] + word[index] + word[index + 2 :]
        for index in range(len(word) - 1)
        if word[index] != word[index + 1]
    }
    return collapsed | transposed


def _expand_investigation(text):
    abbreviation = r"O\s*(?:[/\.]\s*)?I\.?"
    text = re.sub(
        rf"\b(?:DE\s+)?{abbreviation}(?=\s*(?:[.;,]|$))",
        "DE ORIGEN A INVESTIGAR",
        text,
    )
    return text


def learning_diagnosis(value):
    """Keep the clinical diagnosis while excluding a plain anatomical suffix."""
    text = canonical_diagnosis(value)
    words = re.split(r"[\s/]+", text)
    if words[0] not in MUSCULOSKELETAL_ROOTS:
        return text
    start = next(
        (index for index, word in enumerate(words) if word in ANATOMICAL_WORDS),
        len(words),
    )
    if start == len(words):
        return text
    suffix = words[start:]
    if not all(word in LOCATION_WORDS for word in suffix):
        return text
    prefix = words[:start]
    while prefix and prefix[-1] in {"DE", "DEL", "EN", "EL", "LA", "LOS", "LAS"}:
        prefix.pop()
    return " ".join(prefix)


def receipt_diagnosis(value):
    """Render one diagnosis with exactly one sentence-ending period."""
    text = canonical_diagnosis(value)
    return f"{text}." if text else ""
