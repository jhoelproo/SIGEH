"""Select an existing receipt without creating or linking another record."""

from PySide6.QtWidgets import QInputDialog


def choose_linked_receipt(receipts, open_receipt):
    if not receipts:
        return False
    labels = [
        f"Recibo {row['numero']} · {row['ars'] or 'Sin seguro'} · {row['estado_facturacion']} · ID {row['id']}"
        for row in receipts
    ]
    selected, accepted = QInputDialog.getItem(
        None,
        "El paciente ya tiene recibos",
        "Selecciona el recibo que deseas editar:",
        labels,
        0,
        False,
    )
    if not accepted:
        return False
    open_receipt(int(receipts[labels.index(selected)]["id"]))
    return True
