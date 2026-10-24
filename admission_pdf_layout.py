"""Keep the completed admission sheet on Letter without distorting its form."""

from PyPDF2 import Transformation
from PyPDF2.generic import NameObject, RectangleObject

LETTER_POINTS = (612.0, 792.0)


def fit_admission_pages_to_letter(writer) -> None:
    for page in writer.pages:
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        if width <= 0 or height <= 0:
            raise ValueError("La plantilla tiene un tamaño de página inválido.")
        if (width, height) == LETTER_POINTS:
            continue
        scale = min(LETTER_POINTS[0] / width, LETTER_POINTS[1] / height)
        left, bottom = float(page.mediabox.left), float(page.mediabox.bottom)
        x = (LETTER_POINTS[0] - width * scale) / 2
        y = (LETTER_POINTS[1] - height * scale) / 2
        page.add_transformation(
            Transformation().translate(-left, -bottom).scale(scale).translate(x, y)
        )
        # Cloned documents need an indirect stream here. A direct ContentStream
        # can be read by PyPDF2 but is rejected by PDFium and physical viewers.
        if page.get_contents() is not None:
            page[NameObject("/Contents")] = writer._add_object(page.get_contents())
        for box in ("mediabox", "cropbox", "trimbox", "bleedbox", "artbox"):
            setattr(page, box, RectangleObject((0, 0, *LETTER_POINTS)))
