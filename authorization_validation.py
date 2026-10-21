"""One ASCII authorization format for interactive and durable writes."""

MAX_AUTHORIZATION_LENGTH = 40


def numeric_authorization(value, *, allow_empty=True, minimum=1):
    text = str(value or "").strip()
    if not text and allow_empty:
        return ""
    if (
        not text.isascii()
        or not text.isdigit()
        or not minimum <= len(text) <= MAX_AUTHORIZATION_LENGTH
    ):
        raise ValueError(
            "La autorización debe contener únicamente dígitos numéricos (máximo 40)."
        )
    return text


def install_authorization_validator(control):
    from PySide6.QtCore import QRegularExpression
    from PySide6.QtGui import QRegularExpressionValidator

    control.setMaxLength(MAX_AUTHORIZATION_LENGTH)
    control.setValidator(
        QRegularExpressionValidator(QRegularExpression("[0-9]{0,40}"), control)
    )
