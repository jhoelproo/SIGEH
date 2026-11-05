"""Keep the history within the usable desktop, with room for its result table."""

HISTORY_TABLE_MIN_HEIGHT = 180


def fit_history_dialog(window):
    screen = window.screen()
    if screen is None:
        return
    available = screen.availableGeometry()
    width = max(320, available.width() - 24)
    height = max(300, available.height() - 44)
    window.setMinimumSize(min(760, width), min(460, height))
    window.resize(min(1240, width), min(800, height))
    window.move(
        available.x() + (available.width() - window.width()) // 2,
        available.y() + (available.height() - window.height()) // 2,
    )


def bind_dialog_close(window, callback):
    for name in ("closing", "finished"):
        signal = getattr(window, name, None)
        if signal is not None:
            signal.connect(lambda *_args: callback())
