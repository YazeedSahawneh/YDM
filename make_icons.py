"""Regenerate the app and extension icons after changing ACCENT in ydm/config.py.

    python make_icons.py
"""
from PySide6.QtWidgets import QApplication

app = QApplication([])
from ydm.gui.theme import make_icon  # noqa: E402

for size in (16, 32, 48, 128):
    make_icon(size).save(f"extension/icons/icon{size}.png")
make_icon(256).save("assets/ydm.png")
make_icon(256).save("assets/ydm.ico")
print("Icons updated.")
