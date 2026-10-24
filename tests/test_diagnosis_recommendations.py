"""Diagnosis learning is deterministic, bounded, and never adds receipt items."""

from types import SimpleNamespace

import pytest

from diagnosis_recommendation_schema import READY_SQL, SCHEMA_SQL, ensure_schema

from diagnosis_recommendations import (
    DEFAULT_SETTINGS,
    DiagnosisCatalog,
    build_evidence,
    group_key,
    normalize_config,
    recommendations,
)


def config(**changes):
    return normalize_config(
        {
            "diagnoses": [
                {"name": "DIAGNÓSTICO ALFA", "aliases": ["DA"]},
                {"name": "DIAGNÓSTICO BETA", "aliases": ["DB"]},
            ],
            **changes,
        }
    )


def model(groups=None, rules=None, **settings):
    return {
        "config": config(settings=settings, rules=rules or []),
        "groups": groups or {},
    }


def group(uses=6, receipts=10, invoiced=3):
    return {
        "receipts": receipts,
        "items": [
            {
                "category": "Medicamentos",
                "item": "MED A",
                "uses": uses,
                "invoiced": invoiced,
                "pending": uses - invoiced,
                "candidates": 0,
            }
        ],
    }


def test_alias_and_clear_typo_are_canonical_but_unknown_is_preserved():
    catalog = DiagnosisCatalog(config())
    assert (
        catalog.normalize("DA; diagnostico betaa")
        == "DIAGNÓSTICO ALFA; DIAGNÓSTICO BETA"
    )
    assert catalog.normalize("NUEVO DIAGNÓSTICO") == "NUEVO DIAGNÓSTICO"
    assert catalog.first_two("DA; DB; TERCERO") == [
        "diagnostico alfa",
        "diagnostico beta",
    ]
    assert catalog.normalize("DA; DB; TERCERO").endswith("TERCERO")


def test_ambiguous_alias_requires_selection():
    value = config()
    value["diagnoses"][1]["aliases"].append("DA")
    catalog = DiagnosisCatalog(value)
    assert len(catalog.choices("DA")) == 2
    with pytest.raises(ValueError, match="diagnóstico"):
        catalog.normalize("DA")


@pytest.mark.parametrize(
    "receipts,uses,expected",
    [(9, 9, False), (10, 5, False), (10, 6, True), (10, 7, True), (11, 6, False)],
)
def test_count_and_frequency_boundaries(receipts, uses, expected):
    data = model({group_key("diagnostico alfa", "GENERAL"): group(uses, receipts)})
    found = recommendations(data, "DA", "GENERAL", {"Medicamentos": {"MED A": 1}})
    assert bool(found["Medicamentos"]) is expected


def test_manual_priority_block_and_current_catalog_prices_remain_external():
    rules = [
        {
            "diagnosis": "DA",
            "specialty": "GENERAL",
            "category": "Medicamentos",
            "item": "MED B",
            "mode": "pin",
            "priority": 1,
        },
        {
            "diagnosis": "DA",
            "specialty": "GENERAL",
            "category": "Medicamentos",
            "item": "MED A",
            "mode": "block",
            "priority": 0,
        },
    ]
    data = model({group_key("diagnostico alfa", "GENERAL"): group(10)}, rules)
    catalog = {"Medicamentos": {"MED A": 15, "MED B": 27}}
    assert (
        recommendations(data, "DA", "GENERAL", catalog)["Medicamentos"][0]["item"]
        == "MED B"
    )
    assert catalog["Medicamentos"]["MED B"] == 27
    assert not recommendations(data, "DA", "PEDIATRÍA", catalog)["Medicamentos"]
    assert not recommendations(data, "DA", "GENERAL", {"Medicamentos": {"MED A": 15}})[
        "Medicamentos"
    ]


def test_one_medication_per_diagnosis_and_unique_rows():
    rules = [
        {
            "diagnosis": diagnosis,
            "specialty": "GENERAL",
            "category": "Medicamentos",
            "item": item,
            "mode": "pin",
            "priority": i,
        }
        for diagnosis, item, i in [
            ("DA", "MED A", 1),
            ("DA", "MED B", 2),
            ("DB", "MED B", 1),
        ]
    ]
    found = recommendations(
        model(rules=rules),
        "DA; DB; TERCERO",
        "GENERAL",
        {"Medicamentos": {"MED A": 1, "MED B": 2}},
    )
    assert [item["item"] for item in found["Medicamentos"]] == ["MED A", "MED B"]


def test_more_weight_for_invoiced_receipts():
    stats = group(8)
    stats["items"].append(
        {
            "category": "Medicamentos",
            "item": "MED B",
            "uses": 7,
            "invoiced": 7,
            "pending": 0,
            "candidates": 0,
        }
    )
    found = recommendations(
        model({group_key("diagnostico alfa", "GENERAL"): stats}),
        "DA",
        "GENERAL",
        {"Medicamentos": {"MED A": 1, "MED B": 2}},
    )
    assert found["Medicamentos"][0]["item"] == "MED B"


def test_unambiguous_single_diagnosis_can_learn_but_multiple_diagnoses_cannot_guess():
    items = {
        "Medicamentos": ["MED A"],
        "Laboratorios": ["LAB A", "LAB B"],
        "Imágenes": ["IMG A"],
    }
    one = build_evidence(model(), ["diagnostico alfa"], "GENERAL", items)
    assert all(row["confirmed"] for row in one)
    two = build_evidence(
        model(), ["diagnostico alfa", "diagnostico beta"], "GENERAL", items
    )
    assert not any(row["confirmed"] for row in two)


def test_ambiguous_medications_go_to_admin_without_charging_or_guessing():
    rows = build_evidence(
        model(), ["diagnostico alfa"], "GENERAL", {"Medicamentos": ["MED A", "MED B"]}
    )
    assert len(rows) == 2 and not any(row["confirmed"] for row in rows)


def test_configured_relation_identifies_medication_from_two_diagnoses():
    rules = [
        {
            "diagnosis": "DA",
            "specialty": "GENERAL",
            "category": "Medicamentos",
            "item": "MED A",
            "mode": "pin",
            "priority": 1,
        }
    ]
    rows = build_evidence(
        model(rules=rules),
        ["diagnostico alfa", "diagnostico beta"],
        "GENERAL",
        {"Medicamentos": ["MED A", "MED B"]},
    )
    assert [(row["diagnosis"], row["item"]) for row in rows if row["confirmed"]] == [
        ("diagnostico alfa", "MED A")
    ]


@pytest.mark.parametrize(
    "key,value",
    [
        ("min_receipts", 0),
        ("frequency_percent", 101),
        ("pending_weight", -1),
        ("lab_limit", 0),
        ("image_limit", True),
    ],
)
def test_invalid_settings_are_rejected(key, value):
    with pytest.raises(ValueError):
        config(settings={key: value})


def test_defaults_match_approved_parameters():
    assert DEFAULT_SETTINGS == {
        "min_receipts": 10,
        "frequency_percent": 60,
        "invoiced_weight": 2,
        "pending_weight": 1,
        "lab_limit": 3,
        "image_limit": 2,
    }


@pytest.mark.parametrize("invoiced,pending", [(1, 1), (1, 2), (2, 3)])
def test_facturado_weight_must_remain_greater(invoiced, pending):
    with pytest.raises(ValueError, match="peso"):
        config(settings={"invoiced_weight": invoiced, "pending_weight": pending})


def test_block_does_not_make_another_medication_principal_by_elimination():
    rules = [
        {
            "diagnosis": "DA",
            "specialty": "GENERAL",
            "category": "Medicamentos",
            "item": "MED A",
            "mode": "block",
            "priority": 1,
        }
    ]
    rows = build_evidence(
        model(rules=rules),
        ["diagnostico alfa"],
        "GENERAL",
        {"Medicamentos": ["MED A", "MED B"]},
    )
    assert not any(row["confirmed"] for row in rows)


def test_manual_principal_wins_over_previously_learned_relation():
    rules = [
        {
            "diagnosis": "DA",
            "specialty": "GENERAL",
            "category": "Medicamentos",
            "item": "MED B",
            "mode": "pin",
            "priority": 1,
        }
    ]
    data = model({group_key("diagnostico alfa", "GENERAL"): group(10)}, rules)
    rows = build_evidence(
        data, ["diagnostico alfa"], "GENERAL", {"Medicamentos": ["MED A", "MED B"]}
    )
    assert [row["item"] for row in rows if row["confirmed"]] == ["MED B"]


def test_studies_have_limits_and_duplicate_recommendations_merge():
    rules = [
        {
            "diagnosis": dx,
            "specialty": "GENERAL",
            "category": category,
            "item": f"{category} {i}",
            "mode": "pin",
            "priority": i,
        }
        for dx in ("DA", "DB")
        for category in ("Laboratorios", "Imágenes")
        for i in range(1, 6)
    ]
    catalogs = {
        category: {f"{category} {i}": 1 for i in range(1, 6)}
        for category in ("Laboratorios", "Imágenes")
    }
    result = recommendations(model(rules=rules), "DA; DB", "GENERAL", catalogs)
    assert len(result["Laboratorios"]) == 3 and len(result["Imágenes"]) == 2
    assert result["Laboratorios"][0]["diagnoses"] == [
        "DIAGNÓSTICO ALFA",
        "DIAGNÓSTICO BETA",
    ]


@pytest.mark.parametrize("value", ["", "D", "COMPLETAMENTE DIFERENTE"])
def test_empty_short_and_unknown_diagnosis_does_not_invent_recommendations(value):
    assert (
        recommendations(model(), value, "GENERAL", {"Medicamentos": {"MED A": 1}})[
            "Medicamentos"
        ]
        == []
    )


def test_known_conjunction_is_split_without_changing_unknown_diagnostic_phrase():
    catalog = DiagnosisCatalog(config())
    assert catalog.normalize("DA y DB") == "DIAGNÓSTICO ALFA; DIAGNÓSTICO BETA"
    assert catalog.normalize("ALFA y SINTOMA NUEVO") == "ALFA Y SINTOMA NUEVO"
    assert (
        catalog.normalize("diagnostico alfaa", allow_fuzzy=False) == "DIAGNOSTICO ALFAA"
    )
    assert catalog.normalize("DA / DB") == "DIAGNÓSTICO ALFA; DIAGNÓSTICO BETA"
    assert catalog.normalize("S/P PROCEDIMIENTO") == "S/P PROCEDIMIENTO"


@pytest.mark.parametrize(
    "value",
    [
        {"diagnoses": [{"name": ""}]},
        {"diagnoses": [{"name": "A"}, {"name": "a"}]},
        {"diagnoses": [{"name": "A", "aliases": ["x" * 181]}]},
        {"settings": {"unknown": 1}},
        {
            "diagnoses": [{"name": "A"}],
            "rules": [
                {
                    "diagnosis": "B",
                    "specialty": "GENERAL",
                    "category": "Medicamentos",
                    "item": "MED A",
                    "mode": "pin",
                }
            ],
        },
        {
            "diagnoses": [{"name": "A"}],
            "rules": [
                {
                    "diagnosis": "A",
                    "specialty": "GENERAL",
                    "category": "Materiales",
                    "item": "MAT A",
                    "mode": "pin",
                }
            ],
        },
        {
            "diagnoses": [{"name": "A"}],
            "rules": [
                {
                    "diagnosis": "A",
                    "specialty": "GENERAL",
                    "category": "Medicamentos",
                    "item": "",
                    "mode": "pin",
                }
            ],
        },
    ],
)
def test_invalid_diagnoses_and_associations_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_config(value)


def test_manual_study_for_second_diagnosis_precedes_learned_first_diagnosis():
    rules = [
        {
            "diagnosis": "DB",
            "specialty": "GENERAL",
            "category": "Laboratorios",
            "item": "LAB MANUAL",
            "mode": "pin",
            "priority": 1,
        }
    ]
    stats = {
        "receipts": 10,
        "items": [
            {
                "category": "Laboratorios",
                "item": "LAB AUTO",
                "uses": 10,
                "invoiced": 0,
                "pending": 10,
                "candidates": 0,
            }
        ],
    }
    data = model({group_key("diagnostico alfa", "GENERAL"): stats}, rules, lab_limit=1)
    result = recommendations(
        data, "DA; DB", "GENERAL", {"Laboratorios": {"LAB AUTO": 1, "LAB MANUAL": 2}}
    )
    assert result["Laboratorios"][0]["item"] == "LAB MANUAL"


def test_duplicate_rules_are_rejected():
    rule = {
        "diagnosis": "DA",
        "specialty": "GENERAL",
        "category": "Medicamentos",
        "item": "MED A",
        "mode": "pin",
        "priority": 1,
    }
    with pytest.raises(ValueError, match="duplicadas"):
        config(rules=[rule, dict(rule)])


def test_previously_learned_principal_can_resolve_multiple_medications():
    data = model({group_key("diagnostico alfa", "GENERAL"): group(10)})
    rows = build_evidence(
        data, ["diagnostico alfa"], "GENERAL", {"Medicamentos": ["MED A", "MED B"]}
    )
    assert [row["item"] for row in rows if row["confirmed"]] == ["MED A"]


def test_similar_unknown_diagnosis_does_not_show_items_until_normalized():
    rules = [
        {
            "diagnosis": "DA",
            "specialty": "GENERAL",
            "category": "Medicamentos",
            "item": "MED A",
            "mode": "pin",
            "priority": 1,
        }
    ]
    data = model(rules=rules)
    assert not recommendations(
        data, "DIAGNOSTICO ALFAA", "GENERAL", {"Medicamentos": {"MED A": 1}}
    )["Medicamentos"]
    assert recommendations(
        data, "DIAGNÓSTICO ALFA", "GENERAL", {"Medicamentos": {"MED A": 1}}
    )["Medicamentos"]


@pytest.mark.parametrize(
    "readiness,installed",
    [([True], False), ([False, True], False), ([False, False], True)],
)
def test_schema_upgrade_is_once_only_and_rechecks_after_another_station(
    readiness, installed
):
    answers = iter(readiness)
    executed = []

    def execute(query, _parameters=None):
        executed.append(query)
        return SimpleNamespace(fetchone=lambda: (next(answers),))

    assert ensure_schema(SimpleNamespace(execute=execute)) is installed
    assert (SCHEMA_SQL in executed) is installed
    assert executed.count(READY_SQL) == len(readiness)


def test_manual_medication_priority_tie_has_same_principal_in_learning_and_display():
    rules = [
        {
            "diagnosis": "DA",
            "specialty": "GENERAL",
            "category": "Medicamentos",
            "item": item,
            "mode": "pin",
            "priority": 1,
        }
        for item in ("MED B", "MED A")
    ]
    data = model(rules=rules)
    result = recommendations(
        data, "DA", "GENERAL", {"Medicamentos": {"MED A": 1, "MED B": 2}}
    )
    rows = build_evidence(
        data, ["diagnostico alfa"], "GENERAL", {"Medicamentos": ["MED A", "MED B"]}
    )
    assert result["Medicamentos"][0]["item"] == "MED A"
    assert [row["item"] for row in rows if row["confirmed"]] == ["MED A"]


def test_typing_one_unknown_diagnosis_does_not_run_fuzzy_matching(monkeypatch):
    def forbidden_matching(*_args):
        raise AssertionError(
            "Fuzzy matching belongs to diagnosis confirmation, not ordinary typing"
        )

    monkeypatch.setattr("diagnosis_recommendations.SequenceMatcher", forbidden_matching)
    assert not recommendations(
        model(), "DIAGNOSTICO ALFAA", "GENERAL", {"Medicamentos": {"MED A": 1}}
    )["Medicamentos"]


def test_known_compound_diagnosis_is_preserved_when_both_parts_are_also_known():
    catalog = DiagnosisCatalog(
        normalize_config(
            {"diagnoses": [{"name": name} for name in ("ALFA", "BETA", "ALFA Y BETA")]}
        )
    )
    assert catalog.first_two("ALFA Y BETA") == ["alfa y beta"]
