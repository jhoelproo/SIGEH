"""Local diagnosis associations from saved receipts, with explicit ambiguity."""

from difflib import SequenceMatcher
from functools import lru_cache
import re
import unicodedata

from diagnosis_text import BUILTIN_DIAGNOSES, canonical_diagnosis, learning_diagnosis

CATEGORIES = ("Medicamentos", "Laboratorios", "Imágenes")
SPECIALTIES = ("EMERGENCIOLOGÍA", "PEDIATRÍA", "GINECOLOGÍA")
DEFAULT_SETTINGS = {
    "min_receipts": 10,
    "frequency_percent": 60,
    "invoiced_weight": 2,
    "pending_weight": 1,
    "lab_limit": 3,
    "image_limit": 2,
}
SETTING_LIMITS = {
    "min_receipts": (1, 1000),
    "frequency_percent": (1, 100),
    "invoiced_weight": (1, 10),
    "pending_weight": (1, 10),
    "lab_limit": (1, 20),
    "image_limit": (1, 20),
}
MAX_DIAGNOSES = 2
MIN_FUZZY_LENGTH = 5
MIN_FUZZY_SCORE = 0.90
FUZZY_MARGIN = 0.06


def text_key(value):
    plain = unicodedata.normalize("NFKD", str(value or ""))
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", plain.casefold()).strip()


def specialty_key(value):
    key = text_key(value)
    aliases = {
        "": SPECIALTIES[0],
        "general": SPECIALTIES[0],
        "medicina general": SPECIALTIES[0],
    }
    aliases.update({text_key(name): name for name in SPECIALTIES})
    return aliases.get(key, "")


def group_key(diagnosis, specialty):
    return f"{text_key(diagnosis)}::{specialty_key(specialty)}"


def _bounded_integer(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("La configuración requiere números enteros.")
    if not minimum <= value <= maximum:
        raise ValueError(f"El valor debe estar entre {minimum} y {maximum}.")
    return value


def _diagnosis_definition(row, preserve_conflicts):
    name = learning_diagnosis(row.get("name"))
    if not text_key(name) or len(name) > 180:
        raise ValueError("El diagnóstico debe tener entre 1 y 180 caracteres.")
    aliases = sorted(
        {canonical_diagnosis(item) for item in row.get("aliases", []) if text_key(item)}
    )
    if (len(aliases) > 100 and not preserve_conflicts) or any(
        len(item) > 180 for item in aliases
    ):
        raise ValueError("Hay demasiadas equivalencias o son demasiado largas.")
    return {"name": name, "aliases": aliases}


def normalize_config(value, *, preserve_conflicts=False):
    settings = _normalized_settings(value.get("settings") or {})
    diagnoses = _merged_diagnoses(value.get("diagnoses", []), preserve_conflicts)
    result = {"settings": settings, "diagnoses": diagnoses, "rules": []}
    catalog = DiagnosisCatalog(result)
    result["rules"] = _merged_rules(value.get("rules", []), catalog, preserve_conflicts)
    return result


def _merged_diagnoses(rows, preserve_conflicts):
    merged, originals = {}, set()
    for row in rows:
        original = str(row.get("name") or "").strip().upper()
        if original in originals and not preserve_conflicts:
            raise ValueError("Hay diagnósticos duplicados.")
        originals.add(original)
        value = _diagnosis_definition(row, preserve_conflicts)
        key = text_key(value["name"])
        target = merged.setdefault(key, {"name": value["name"], "aliases": []})
        aliases = set(target["aliases"]) | set(value["aliases"])
        target["aliases"] = sorted(alias for alias in aliases if text_key(alias) != key)
        if len(target["aliases"]) > 100 and not preserve_conflicts:
            raise ValueError("Hay demasiadas equivalencias o son demasiado largas.")
    return list(merged.values())


def _merged_rules(rows, catalog, preserve_conflicts):
    merged, sources = {}, {}
    for row in rows:
        value = _normalized_rule(row, catalog)
        identity = tuple(
            value[field] for field in ("diagnosis", "specialty", "category")
        )
        identity += (text_key(value["item"]),)
        if preserve_conflicts:
            identity += (value["mode"],)
        source = str(row.get("diagnosis") or "").strip().upper()
        if identity in merged:
            _merge_equivalent_rule(
                merged[identity],
                value,
                source in sources[identity] and not preserve_conflicts,
            )
            sources[identity].add(source)
        else:
            merged[identity], sources[identity] = value, {source}
    return list(merged.values())


def _merge_equivalent_rule(previous, value, repeated_source):
    if repeated_source or previous["mode"] != value["mode"]:
        raise ValueError("Hay asociaciones duplicadas o contradictorias.")
    previous["priority"] = min(previous["priority"], value["priority"])


def _normalized_settings(value):
    settings = {**DEFAULT_SETTINGS, **dict(value)}
    if set(settings) != set(DEFAULT_SETTINGS):
        raise ValueError("Hay parámetros de recomendaciones desconocidos.")
    for name, limits in SETTING_LIMITS.items():
        settings[name] = _bounded_integer(settings[name], *limits)
    if settings["invoiced_weight"] <= settings["pending_weight"]:
        raise ValueError("El peso de facturados debe ser mayor que el de pendientes.")
    return settings


def _normalized_rule(row, catalog):
    diagnosis = text_key(
        learning_diagnosis(catalog.normalize(str(row.get("diagnosis") or "")))
    )
    specialty = specialty_key(row.get("specialty"))
    category = str(row.get("category") or "")
    mode = str(row.get("mode") or "")
    item = str(row.get("item") or "").strip()
    _validate_rule_context(catalog, diagnosis, specialty, category, mode)
    if not text_key(item) or len(item) > 500:
        raise ValueError("Selecciona un ítem del catálogo.")
    return {
        "diagnosis": diagnosis,
        "specialty": specialty,
        "category": category,
        "item": item,
        "mode": mode,
        "priority": _bounded_integer(row.get("priority", 1), 0, 9999),
    }


def _validate_rule_context(catalog, diagnosis, specialty, category, mode):
    if diagnosis not in catalog.names or not specialty:
        raise ValueError("Selecciona un diagnóstico y especialidad válidos.")
    if category not in CATEGORIES or mode not in {"pin", "block"}:
        raise ValueError("La categoría o acción de la asociación no es válida.")


def _one_edit_apart(left, right):
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(first != second for first, second in zip(left, right)) == 1
    shorter, longer = sorted((left, right), key=len)
    return any(
        shorter == longer[:index] + longer[index + 1 :] for index in range(len(longer))
    )


@lru_cache(maxsize=4096)
def _complete_suggestion(name):
    if name in BUILTIN_DIAGNOSES:
        return True
    if re.search(r"[/;|+?]|\b(?:VS|PB)\b|\bORIGEN INVESTIGAR\b", name):
        return False
    final = name.split()[-1]
    if len(final) == 1 and not re.search(
        r"\b(?:TIPO|GRADO|HEPATITIS)\s+[A-EIVX]$", name
    ):
        return False
    if final in {"DE", "DEL", "CON", "SIN", "TIPO", "POR"}:
        return False
    key = text_key(name)
    return not any(
        _one_edit_apart(key, text_key(candidate)) for candidate in BUILTIN_DIAGNOSES
    )


class DiagnosisCatalog:
    def __init__(self, config, learned_names=()):
        self.trusted_labels = tuple(
            canonical_diagnosis(row["name"]) for row in config.get("diagnoses", [])
        )
        self.names = {
            text_key(name): name
            for source in (*BUILTIN_DIAGNOSES, *learned_names)
            if text_key(name := self.learning_label(source))
        }
        self.aliases = {}
        self.canonical_keys = {
            text_key(self.learning_label(row["name"]))
            for row in config.get("diagnoses", [])
        }
        for row in config.get("diagnoses", []):
            name = self.learning_label(row["name"])
            self.names[text_key(name)] = name
            for alias in [name, *row.get("aliases", [])]:
                self.aliases.setdefault(
                    text_key(self._canonical_label(alias)), set()
                ).add(name)
        self._prepare_suggestions()

    def _prepare_suggestions(self):
        self.suggestion_names = {
            key: name
            for key, name in self.names.items()
            if (key in self.canonical_keys or name in BUILTIN_DIAGNOSES)
            and _complete_suggestion(name)
        }
        self._suggestion_entries = tuple(self.suggestion_names.items()) + tuple(
            (alias, name)
            for alias, names in self.aliases.items()
            for name in names
            if text_key(name) in self.suggestion_names
        )
        self._suggestion_cache = {}

    def _canonical_label(self, value):
        return canonical_diagnosis(value)

    def learning_label(self, value):
        """Correct trusted spelling for stored evidence without guessing a subtype."""
        return learning_diagnosis(
            canonical_diagnosis(value, trusted_labels=self.trusted_labels)
        )

    def choices(self, value):
        canonical = self._canonical_label(value)
        key = text_key(canonical)
        if learning_diagnosis(canonical) != canonical:
            return [canonical]
        if key in self.canonical_keys:
            return [self.names[key]]
        exact = self.aliases.get(key)
        if exact:
            return sorted(exact)
        if key in self.names:
            return [self.names[key]]
        return self._fuzzy_choices(key)

    def suggestions(self, value, limit=12):
        if limit <= 0:
            return []
        key = text_key(self._canonical_label(value))
        if not key:
            return []
        if key not in self._suggestion_cache:
            if len(self._suggestion_cache) >= 128:
                self._suggestion_cache.clear()
            self._suggestion_cache[key] = self._suggestion_matches(key)
        return list(self._suggestion_cache[key][:limit])

    def _suggestion_matches(self, key):
        matches = {
            name for candidate, name in self._suggestion_entries if key in candidate
        }
        if not matches:
            matches = self._fuzzy_choices(key)
        return tuple(
            sorted(
                set(matches),
                key=lambda name: (not text_key(name).startswith(key), name),
            )
        )

    def _fuzzy_choices(self, key):
        if len(key) < MIN_FUZZY_LENGTH:
            return []
        scores = sorted(
            (
                (SequenceMatcher(None, key, candidate).ratio(), name)
                for candidate, name in self.suggestion_names.items()
            ),
            reverse=True,
        )
        if not scores or scores[0][0] < MIN_FUZZY_SCORE:
            return []
        best = scores[0][0]
        return sorted({name for score, name in scores if best - score < FUZZY_MARGIN})

    def split(self, value):
        text = re.sub(
            r"\b(?:DE\s+)?O\s*[/\.]\s*I\.?(?=\s*(?:[.;,]|$))",
            "DE ORIGEN A INVESTIGAR",
            str(value or "").strip(),
            flags=re.IGNORECASE,
        )
        pieces = [
            part.strip()
            for part in re.split(r";|\n|\s*//\s*|\s*\|\s*|\s+/\s+", text)
            if part.strip()
        ]
        if len(pieces) == 1:
            pieces = self._split_known_conjunction(text)
        return pieces

    def _split_known_conjunction(self, text):
        parts = re.split(r"\s+(?:y|Y)\s+|\s+\+\s+", text)
        if len(parts) < 2 or self.choices(text):
            return [text]
        return parts if all(self.choices(part) for part in parts) else [text]

    def normalize(self, value, *, allow_fuzzy=True):
        result = []
        for part in self.split(value):
            canonical = self._canonical_label(part)
            exact = (
                text_key(canonical) in self.names or text_key(canonical) in self.aliases
            )
            choices = self.choices(part) if allow_fuzzy or exact else []
            if len(choices) > 1:
                raise ValueError(
                    f"Selecciona el diagnóstico correspondiente a «{part}»."
                )
            result.append(choices[0] if choices else canonical)
        return "; ".join(result)

    def first_two(self, value, *, allow_fuzzy=True):
        return list(
            dict.fromkeys(
                text_key(learning_diagnosis(part))
                for part in self.split(self.normalize(value, allow_fuzzy=allow_fuzzy))
            )
        )[:MAX_DIAGNOSES]


def model_catalog(model):
    names = [row.get("diagnosis", "") for row in model.get("groups", {}).values()]
    return DiagnosisCatalog(model.get("config", {}), names)


def _rules_for(config, diagnosis, specialty, category):
    return [
        row
        for row in config.get("rules", [])
        if (row["diagnosis"], row["specialty"], row["category"])
        == (diagnosis, specialty, category)
    ]


def _eligible_learned(group, category, settings):
    total = int(group.get("receipts", 0))
    if total < settings["min_receipts"]:
        return []
    return [
        row
        for row in group.get("items", [])
        if row["category"] == category
        and row["uses"] * 100 >= total * settings["frequency_percent"]
    ]


def _pinned_rules(rules):
    return sorted(
        (row for row in rules if row["mode"] == "pin"),
        key=lambda row: (row["priority"], row["item"]),
    )


def _ranked_group(model, diagnosis, specialty, category):
    config = model.get("config", {})
    settings = {**DEFAULT_SETTINGS, **config.get("settings", {})}
    rules = _rules_for(config, diagnosis, specialty, category)
    blocked = {text_key(row["item"]) for row in rules if row["mode"] == "block"}
    pins = _pinned_rules(rules)
    group = model.get("groups", {}).get(group_key(diagnosis, specialty), {})
    learned = _eligible_learned(group, category, settings)
    learned.sort(
        key=lambda row: (
            -row["invoiced"] * settings["invoiced_weight"]
            - row["pending"] * settings["pending_weight"],
            -row["uses"],
            row["item"],
        )
    )
    values = [(row["item"], "manual") for row in pins] + [
        (row["item"], "learned") for row in learned
    ]
    return list(
        dict.fromkeys(
            (item, origin) for item, origin in values if text_key(item) not in blocked
        )
    )


def _category_limit(category, settings):
    return {
        "Medicamentos": MAX_DIAGNOSES,
        "Laboratorios": settings["lab_limit"],
        "Imágenes": settings["image_limit"],
    }[category]


def recommendations(model, diagnoses, specialty, catalogs, *, catalog=None):
    result = {category: [] for category in CATEGORIES}
    specialty = specialty_key(specialty)
    catalog = catalog if catalog is not None else model_catalog(model)
    keys = catalog.first_two(diagnoses, allow_fuzzy=False)
    settings = {**DEFAULT_SETTINGS, **model.get("config", {}).get("settings", {})}
    for category in CATEGORIES:
        available = {text_key(item): item for item in catalogs.get(category, {})}
        result[category] = _recommend_category(
            model, keys, specialty, category, available, catalog
        )
        result[category] = result[category][: _category_limit(category, settings)]
    return result


def _recommend_category(model, keys, specialty, category, available, catalog):
    result = {}
    for diagnosis in keys:
        added = 0
        for item, origin in _ranked_group(model, diagnosis, specialty, category):
            name = available.get(text_key(item))
            if name is None:
                continue
            label = catalog.names.get(diagnosis, diagnosis.upper())
            row = result.setdefault(
                name, {"item": name, "diagnoses": [], "origin": origin}
            )
            if origin == "manual":
                row["origin"] = "manual"
            if label not in row["diagnoses"]:
                row["diagnoses"].append(label)
            added += 1
            if category == "Medicamentos" and added == 1:
                break
    return sorted(result.values(), key=lambda row: row["origin"] != "manual")


def build_evidence(model, diagnoses, specialty, items):
    rows = []
    specialty = specialty_key(specialty)
    for diagnosis in diagnoses[:MAX_DIAGNOSES]:
        for category in CATEGORIES:
            names = list(dict.fromkeys(items.get(category, [])))
            confirmed = _confirmed_items(
                model, diagnosis, specialty, category, names, len(diagnoses)
            )
            rows.extend(
                {
                    "diagnosis": diagnosis,
                    "specialty": specialty,
                    "category": category,
                    "item": name,
                    "confirmed": name in confirmed,
                }
                for name in names
            )
    return rows


def _confirmed_items(model, diagnosis, specialty, category, names, diagnosis_count):
    rules = _rules_for(model.get("config", {}), diagnosis, specialty, category)
    allowed = _allowed_items(rules, names)
    known = {
        text_key(item)
        for item, _origin in _ranked_group(model, diagnosis, specialty, category)
    }
    matched = [name for name in allowed if text_key(name) in known]
    if category == "Medicamentos":
        principal = _manual_principal(rules, allowed)
        if principal:
            return [principal]
        return _principal_medication(allowed, matched, diagnosis_count, len(names))
    return allowed if diagnosis_count == 1 else matched


def _allowed_items(rules, names):
    blocked = {text_key(row["item"]) for row in rules if row["mode"] == "block"}
    return [name for name in names if text_key(name) not in blocked]


def _manual_principal(rules, allowed):
    pins = _pinned_rules(rules)
    return next(
        (
            name
            for rule in pins
            for name in allowed
            if text_key(name) == text_key(rule["item"])
        ),
        None,
    )


def _principal_medication(allowed, matched, diagnosis_count, original_count):
    if len(matched) == 1:
        return matched
    if diagnosis_count == 1 and original_count == 1 and len(allowed) == 1:
        return allowed
    return []
