"""Diagnosis spelling corrections keep administrative and clinical identities apart."""

import pytest

from diagnosis_recommendations import (
    DiagnosisCatalog,
    model_catalog,
    normalize_config,
    recommendations,
    text_key,
)
from diagnosis_text import canonical_diagnosis, learning_diagnosis, receipt_diagnosis


@pytest.mark.parametrize(
    "value,expected",
    [
        (" aborto imcompleto .. ", "ABORTO INCOMPLETO"),
        ("ABORTO INCOMPLETO.", "ABORTO INCOMPLETO"),
        ("crisis de sibilancia", "CRISIS DE SIBILANCIAS"),
        ("CRISIS DE SIBILANCIAS.", "CRISIS DE SIBILANCIAS"),
        ("CRISIS DE BRONCO ESPAMAS ..", "CRISIS DE BRONCOESPASMO"),
        ("CRISIS DE BRONCOESPASM", "CRISIS DE BRONCOESPASMO"),
        ("CRISIS HIPERTENCIVA", "CRISIS HIPERTENSIVA"),
        ("CRISIS HIPERTENTIVA TIPO URGENCIA", "CRISIS HIPERTENSIVA TIPO URGENCIA"),
        ("CRISIS HIPERTENSIVA TIPO URGERCIA", "CRISIS HIPERTENSIVA TIPO URGENCIA"),
        ("CRISIS HTA TIPO EMERGENCIA", "CRISIS HIPERTENSIVA TIPO EMERGENCIA"),
        ("ALTRAGIA MANO IZURDO", "ARTRALGIA MANO IZQUIERDO"),
        ("AMIGADALITIS AGUDA", "AMIGDALITIS AGUDA"),
        ("AMIGDALITIS AGUDO", "AMIGDALITIS AGUDA"),
        ("AMIGDALITIS AGUDA CON EXPUDADO.", "AMIGDALITIS AGUDA CON EXUDADO"),
        ("nuevo. diagnostico...", "NUEVO DIAGNOSTICO"),
        ("EMBARAZO 37.5 SEMANAS.", "EMBARAZO 37.5 SEMANAS"),
        ("NUEVO DIAGNOSTICO 1.2.3", "NUEVO DIAGNOSTICO 1.2.3"),
    ],
)
def test_known_spelling_and_sentence_punctuation(value, expected):
    assert canonical_diagnosis(value) == expected
    assert canonical_diagnosis(expected) == expected


@pytest.mark.parametrize("ending", ["OI", "O/I", "O / I", "O.I.", "O. I."])
@pytest.mark.parametrize("prefix", ["CEFALEA DE ", "DOLOR ABDOMINAL "])
def test_investigation_abbreviation_is_expanded_once(prefix, ending):
    expected = prefix.removesuffix("DE ") + "DE ORIGEN A INVESTIGAR"
    assert canonical_diagnosis(prefix + ending) == expected
    assert canonical_diagnosis(expected) == expected
    assert receipt_diagnosis(prefix + ending) == expected + "."


@pytest.mark.parametrize("value", ["", None, " ... ", "."])
def test_empty_diagnosis_is_not_a_period_or_choice(value):
    assert canonical_diagnosis(value) == ""
    assert learning_diagnosis(value) == ""
    assert receipt_diagnosis(value) == ""


def test_unknown_abbreviations_and_internal_words_are_not_expanded():
    assert canonical_diagnosis("S/P PROCEDIMIENTO") == "S/P PROCEDIMIENTO"
    assert canonical_diagnosis("ADENOIDES") == "ADENOIDES"
    assert canonical_diagnosis("OI EN OTRA FRASE") == "OI EN OTRA FRASE"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("ALTRALGIA DE BRAZO IZQUIERDO", "ARTRALGIA"),
        ("ARTRALGIA PIERNA IZQUIERDA", "ARTRALGIA"),
        ("ARTROSIS DE RODILLA DERECHA", "ARTROSIS"),
        ("TRAUMA CERRADO DE BRAZO IZQUIERDO", "TRAUMA CERRADO"),
        ("TRAUMATISMO EN LA MANO DERECHA", "TRAUMATISMO"),
        ("TRAUMA CRANEOENCEFALICO", "TRAUMA CRANEOENCEFALICO"),
        ("TRAUMA DE BRAZO CON FRACTURA", "TRAUMA DE BRAZO CON FRACTURA"),
        ("DOLOR DE PIERNA IZQUIERDA", "DOLOR DE PIERNA IZQUIERDA"),
        ("ARTRALGIA", "ARTRALGIA"),
        ("ESGUINCE DE TOBILLO DERECHO", "ESGUINCE"),
        ("FRACTURA CERRADA DE BRAZO IZQUIERDO", "FRACTURA CERRADA"),
        ("CONTUSIÓN DE MANO DERECHA", "CONTUSIÓN"),
        ("CONTUSION DE PIE IZQUIERDO", "CONTUSIÓN"),
        ("LUXACIÓN DEL HOMBRO DERECHO", "LUXACIÓN"),
        ("LUXACION DE CODO IZQUIERDO", "LUXACIÓN"),
        ("FRACTURA ABIERTA DE FÉMUR", "FRACTURA ABIERTA"),
        ("FRACTURA DE TIBIA Y PERONÉ", "FRACTURA"),
        ("ESGUINCE GRADO II DE TOBILLO DERECHO", "ESGUINCE GRADO II"),
        (
            "FRACTURA DE BRAZO CON DESPLAZAMIENTO",
            "FRACTURA DE BRAZO CON DESPLAZAMIENTO",
        ),
        ("CONTUSIÓN DE PIERNA CON HEMATOMA", "CONTUSIÓN DE PIERNA CON HEMATOMA"),
        ("ARTRALGIA BRAZO/PIE IZQUIERDO", "ARTRALGIA"),
    ],
)
def test_location_is_removed_only_from_learning_and_preserves_subtype(value, expected):
    assert learning_diagnosis(value) == expected
    assert receipt_diagnosis(value) == canonical_diagnosis(value) + "."


def test_localized_receipt_text_is_retained_while_learning_collapses_body_sites():
    catalog = DiagnosisCatalog({})
    text = "ALTRALGIA DE BRAZO IZQUIERDO; ARTRALGIA DE PIE DERECHO; OTRO"
    assert catalog.normalize(text, allow_fuzzy=False).startswith(
        "ARTRALGIA DE BRAZO IZQUIERDO; ARTRALGIA DE PIE DERECHO"
    )
    assert catalog.first_two(text, allow_fuzzy=False) == ["artralgia", "otro"]
    assert catalog.choices("ALTRALGIA DE BRAZO IZQUIERDO") == [
        "ARTRALGIA DE BRAZO IZQUIERDO"
    ]


def test_spaced_slash_in_investigation_is_not_a_diagnosis_separator():
    catalog = DiagnosisCatalog({})
    text = "CEFALEA DE O / I; ABORTO IMCOMPLETO."
    assert catalog.first_two(text) == [
        "cefalea de origen a investigar",
        "aborto incompleto",
    ]


def test_wrong_learned_names_are_not_kept_as_canonical_options():
    catalog = DiagnosisCatalog(
        {},
        ["ABORTO IMCOMPLETO", "ABORTO INCOMPLETO.", "ALTRALGIA DE BRAZO IZQUIERDO"],
    )
    assert "aborto imcompleto" not in catalog.names
    assert "artralgia de brazo izquierdo" not in catalog.names
    assert catalog.choices("ABORTO IMCOMPLETO") == ["ABORTO INCOMPLETO"]
    assert catalog.suggestions("aborto incompleto") == ["ABORTO INCOMPLETO"]


def test_cefalea_completion_shows_explicit_variants_without_selecting_one():
    catalog = DiagnosisCatalog({})
    assert set(catalog.suggestions("cefalea")) == {
        "CEFALEA",
        "CEFALEA MIGRAÑOSA",
        "CEFALEA TENSIONAL",
        "CEFALEA INTENSA",
        "CEFALEA DE ORIGEN A INVESTIGAR",
    }
    assert catalog.normalize("CEFALEA") == "CEFALEA"


def test_hypertension_subtypes_are_not_merged_or_inferred():
    catalog = DiagnosisCatalog({})
    values = (
        "CRISIS HIPERTENSIVA",
        "CRISIS HIPERTENSIVA TIPO URGENCIA",
        "CRISIS HIPERTENSIVA TIPO EMERGENCIA",
    )
    assert all(catalog.normalize(value) == value for value in values)
    assert len({text_key(canonical_diagnosis(value)) for value in values}) == 3


def test_suggestions_match_alias_substrings_and_obey_bounds():
    catalog = DiagnosisCatalog(
        {"diagnoses": [{"name": "DIAGNÓSTICO ALFA", "aliases": ["DALFA"]}]}
    )
    assert catalog.suggestions("dalf") == ["DIAGNÓSTICO ALFA"]
    assert catalog.suggestions("cefalea", limit=1) == ["CEFALEA"]
    assert catalog.suggestions("", limit=1) == []
    assert catalog.suggestions("cefalea", limit=0) == []
    assert catalog.suggestions("cefalea", limit=-1) == []
    assert catalog.suggestions("diagnostico alfaa") == ["DIAGNÓSTICO ALFA"]


def test_equivalent_definitions_merge_aliases_and_associations():
    diagnoses = [
        {"name": "ABORTO IMCOMPLETO", "aliases": ["ABI"]},
        {"name": "ABORTO INCOMPLETO.", "aliases": ["ABIN"]},
    ]
    rules = [
        {
            "diagnosis": row["name"],
            "specialty": "GENERAL",
            "category": "Medicamentos",
            "item": "PRODUCTO QA",
            "mode": "pin",
            "priority": priority,
        }
        for row, priority in zip(diagnoses, (3, 1), strict=True)
    ]
    value = normalize_config({"diagnoses": diagnoses, "rules": rules})
    assert value["diagnoses"] == [
        {"name": "ABORTO INCOMPLETO", "aliases": ["ABI", "ABIN"]}
    ]
    assert len(value["rules"]) == 1
    assert value["rules"][0]["priority"] == 1


def test_localized_definitions_collapse_without_losing_aliases():
    value = normalize_config(
        {
            "diagnoses": [
                {"name": "ALTRALGIA DE BRAZO IZQUIERDO", "aliases": ["AR"]},
                {"name": "ARTRALGIA DE PIE DERECHO", "aliases": ["AR2"]},
            ]
        }
    )
    assert value["diagnoses"] == [{"name": "ARTRALGIA", "aliases": ["AR", "AR2"]}]


def test_collapsing_spelling_does_not_hide_contradictory_rules():
    rule = {
        "specialty": "GENERAL",
        "category": "Medicamentos",
        "item": "QA",
        "mode": "pin",
    }
    with pytest.raises(ValueError, match="contradictorias"):
        normalize_config(
            {
                "diagnoses": [
                    {"name": "ABORTO INCOMPLETO"},
                    {"name": "ABORTO IMCOMPLETO"},
                ],
                "rules": [
                    {**rule, "diagnosis": "ABORTO INCOMPLETO"},
                    {**rule, "diagnosis": "ABORTO IMCOMPLETO", "mode": "block"},
                ],
            }
        )


def test_punctuation_variants_merge_and_receipt_sentence_is_added_once():
    value = normalize_config(
        {"diagnoses": [{"name": "CEFALEA"}, {"name": "CEFALEA..."}]}
    )
    assert value["diagnoses"] == [{"name": "CEFALEA", "aliases": []}]
    assert receipt_diagnosis("CEFALEA...") == "CEFALEA."


def test_merging_definitions_cannot_exceed_alias_limit():
    with pytest.raises(ValueError, match="equivalencias"):
        normalize_config(
            {
                "diagnoses": [
                    {"name": "CEFALEA", "aliases": [f"A{i}" for i in range(100)]},
                    {"name": "CEFALEA.", "aliases": ["ADICIONAL"]},
                ]
            }
        )


@pytest.mark.parametrize(
    "misspelling,correct",
    [
        ("ABORTO INCOMPLETTO", "ABORTO INCOMPLETO"),
        ("AMIGDALLITIS", "AMIGDALITIS"),
        ("CEFALEA TENSOINAL", "CEFALEA TENSIONAL"),
    ],
)
def test_historical_repeated_letter_or_transposition_is_not_a_trusted_anchor(
    misspelling, correct
):
    catalog = model_catalog({"groups": {"old": {"diagnosis": misspelling}}})
    assert text_key(misspelling) not in catalog.names
    assert catalog.normalize(misspelling, allow_fuzzy=False) == correct
    assert correct in catalog.suggestions(misspelling)
    assert misspelling not in catalog.suggestions(misspelling)
    assert catalog.choices(misspelling) == [correct]


@pytest.mark.parametrize(
    "value",
    [
        "ABORTO COMPLETO",
        "ABORTO INCOMPLETO",
        "CRISIS HIPOTENSIVA",
        "CRISIS HIPERTENSIVA",
        "CRISIS HIPERTENSIVA TIPO URGENCIA",
        "CRISIS HIPERTENSIVA TIPO EMERGENCIA",
        "PALABRA SIN VOCABULARIO",
        "ED",
    ],
)
def test_safe_spelling_does_not_apply_substitutions_or_prefix_insertions(value):
    assert canonical_diagnosis(value) == value


def test_configured_vocabulary_can_correct_a_unique_safe_spelling():
    value = {"diagnoses": [{"name": "DIAGNÓSTICO PERSONALIZADO", "aliases": []}]}
    catalog = DiagnosisCatalog(value, ["DIAGNOSTICO PERSONALLIZADO"])
    assert "diagnostico personallizado" not in catalog.names
    assert catalog.normalize("DIAGNOSTICO PERSONALLIZADO", allow_fuzzy=False) == (
        "DIAGNOSTICO PERSONALLIZADO"
    )
    assert catalog.choices("DIAGNOSTICO PERSONALLIZADO") == [
        "DIAGNÓSTICO PERSONALIZADO"
    ]


def test_two_equally_trusted_transpositions_are_not_resolved_automatically():
    value = canonical_diagnosis("ABCDE", trusted_labels=("BACDE", "ACBDE"))
    assert value == "ABCDE"


def test_tolerant_read_preserves_conflicting_rules_and_is_idempotent():
    rule = {
        "specialty": "GENERAL",
        "category": "Medicamentos",
        "item": "PRODUCTO QA",
        "priority": 2,
    }
    source = {
        "diagnoses": [{"name": "ARTRALGIA"}, {"name": "ALTRALGIA"}],
        "rules": [
            {**rule, "diagnosis": "ARTRALGIA", "mode": "pin"},
            {**rule, "diagnosis": "ALTRALGIA", "mode": "block"},
            {**rule, "diagnosis": "ALTRALGIA", "mode": "block", "priority": 1},
        ],
    }
    tolerant = normalize_config(source, preserve_conflicts=True)
    assert tolerant["diagnoses"] == [{"name": "ARTRALGIA", "aliases": []}]
    assert len(tolerant["rules"]) == 2
    assert {row["mode"] for row in tolerant["rules"]} == {"pin", "block"}
    assert normalize_config(tolerant, preserve_conflicts=True) == tolerant
    with pytest.raises(ValueError, match="contradictorias"):
        normalize_config(tolerant)
    assert not recommendations(
        {"config": tolerant},
        "ARTRALGIA",
        "GENERAL",
        {"Medicamentos": {"PRODUCTO QA": 1}},
    )["Medicamentos"]


def test_tolerant_read_merges_identical_old_definitions():
    value = normalize_config(
        {"diagnoses": [{"name": "ARTRALGIA"}, {"name": "ARTRALGIA"}]},
        preserve_conflicts=True,
    )
    assert value["diagnoses"] == [{"name": "ARTRALGIA", "aliases": []}]


def test_learning_label_corrects_configured_spelling_without_auto_normalizing_typing():
    catalog = DiagnosisCatalog(
        {"diagnoses": [{"name": "GASTROENTERITIS"}, {"name": "DIAGNÓSTICO ALFA"}]}
    )
    assert catalog.learning_label("GASTROENTERITTIS") == "GASTROENTERITIS"
    assert (
        catalog.normalize("DIAGNOSTICO ALFAA", allow_fuzzy=False) == "DIAGNOSTICO ALFAA"
    )


def test_tolerant_read_keeps_more_than_one_hundred_merged_legacy_aliases():
    source = {
        "diagnoses": [
            {"name": "CEFALEA", "aliases": [f"A{i}" for i in range(100)]},
            {"name": "CEFALEA.", "aliases": ["ADICIONAL"]},
        ]
    }
    value = normalize_config(source, preserve_conflicts=True)
    assert len(value["diagnoses"][0]["aliases"]) == 101
    assert normalize_config(value, preserve_conflicts=True) == value
    with pytest.raises(ValueError, match="equivalencias"):
        normalize_config(value)
