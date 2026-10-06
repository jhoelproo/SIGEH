"""Local ARS candidate lookup without changing eligibility or selection."""

import re
import unicodedata


def normalized_name(value):
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join(
        "".join(char for char in decomposed if not unicodedata.combining(char)).split()
    )


def normalized_number(value):
    return re.sub(r"\D+", "", str(value or ""))


def number_matches(candidates, fields, needle, *, exact):
    def matches(candidate):
        values = [normalized_number(candidate.get(field)) for field in fields]
        return needle in values if exact else any(needle in value for value in values)

    return [candidate for candidate in candidates if matches(candidate)]


def filter_candidates(candidates, search_mode="ALL", term=""):
    mode = str(search_mode or "ALL").upper()
    text = str(term or "").strip()
    if not text:
        return list(candidates), None
    if mode == "NAME" or (mode == "ALL" and not normalized_number(text)):
        words = normalized_name(text).split()
        return [
            candidate
            for candidate in candidates
            if all(word in normalized_name(candidate.get("nombre")) for word in words)
        ], None
    return filter_numbers(candidates, mode, normalized_number(text))


def filter_numbers(candidates, mode, needle):
    if mode == "RECEIPT":
        return _filter_receipt_numbers(candidates, needle)
    fields = {"NSS": ("nss_snapshot",), "CEDULA": ("cedula_snapshot",)}
    if mode in fields:
        return _filter_document_numbers(candidates, fields[mode], needle)
    return _filter_all_numbers(candidates, needle)


def _best_number_matches(candidates, fields, needle):
    return number_matches(candidates, fields, needle, exact=True) or number_matches(
        candidates, fields, needle, exact=False
    )


def _filter_receipt_numbers(candidates, needle):
    if not needle:
        return [], "Escriba el número de recibo."
    return _best_number_matches(candidates, ("numero",), needle), None


def _filter_document_numbers(candidates, fields, needle):
    if len(needle) < 4:
        return [], "Escriba al menos 4 dígitos."
    return _best_number_matches(candidates, fields, needle), None


def _filter_all_numbers(candidates, needle):
    fields = ("numero", "nss_snapshot", "cedula_snapshot")
    exact = number_matches(candidates, fields, needle, exact=True) if needle else []
    if exact:
        return exact, None
    partial_fields = fields if len(needle) >= 4 else ("numero",)
    partial = (
        number_matches(candidates, partial_fields, needle, exact=False)
        if needle
        else []
    )
    warning = "Escriba al menos 4 dígitos." if len(needle) < 4 and not partial else None
    return partial, warning
