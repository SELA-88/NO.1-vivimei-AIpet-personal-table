import math
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

from .emotion_store import EMOTION_OPTIONS


class EmotionBubbleButton(QPushButton):
    def __init__(self, option, store, parent=None):
        super().__init__(parent)
        self.option = option
        self.setObjectName(f"emotionBubble_{option['slug']}")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        icon_path = store.asset_path(option["label"])
        if icon_path.is_file():
            self.setIcon(QIcon(str(icon_path)))
            self.setIconSize(QSize(54, 54))
        else:
            self.setText(option["label"][:1])
        self.set_selected(False)

    def set_selected(self, selected):
        size = 76 if selected else 66
        self.setFixedSize(size, size)
        self.setIconSize(QSize(size - 10, size - 10))
        background = "#F2FFFC" if selected else "#FFFDF8"
        border = "#18B8A9" if selected else "#D4C9B4"
        width = 4 if selected else 2
        self.setStyleSheet(
            "QPushButton {"
            f"background:{background}; border:{width}px solid {border};"
            f"border-radius:{size // 2}px; padding:0;"
            "}"
            "QPushButton:hover { border-color:#69CBBE; background:#F8FFFD; }"
        )


class EmotionWheelOverlay(QWidget):
    emotionSelected = pyqtSignal(str)
    cancelled = pyqtSignal()

    SIZE = 364
    RADIUS = 118

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self._selected_index = 0
        self.buttons = []
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.resize(self.SIZE, self.SIZE)

        for index, option in enumerate(EMOTION_OPTIONS):
            button = EmotionBubbleButton(option, store, self)
            button.clicked.connect(lambda _checked=False, i=index: self.choose(i))
            self.buttons.append(button)
        self._layout_buttons()
        self._update_selection()

    def _button_center(self, index):
        angle = math.radians(-90 + index * (360 / len(self.buttons)))
        center = self.SIZE / 2
        return (
            round(center + self.RADIUS * math.cos(angle)),
            round(center + self.RADIUS * math.sin(angle)),
        )

    def _layout_buttons(self):
        for index, button in enumerate(self.buttons):
            x, y = self._button_center(index)
            button.move(x - button.width() // 2, y - button.height() // 2)

    def current_emotion(self):
        return EMOTION_OPTIONS[self._selected_index]["label"]

    def cycle_selection(self, delta):
        if not self.buttons:
            return
        self._selected_index = (self._selected_index + int(delta)) % len(self.buttons)
        self._update_selection()

    def choose(self, index):
        if 0 <= index < len(self.buttons):
            self._selected_index = index
            self._update_selection()
            self.confirm_current()

    def _update_selection(self):
        for index, button in enumerate(self.buttons):
            button.set_selected(index == self._selected_index)
        self._layout_buttons()
        self.update()

    def confirm_current(self):
        emotion = self.current_emotion()
        self.hide()
        self.emotionSelected.emit(emotion)

    def cancel(self):
        self.hide()
        self.cancelled.emit()

    def show_at(self, global_center):
        screen = QApplication.screenAt(global_center)
        if screen is None:
            screen = QApplication.primaryScreen()
        available = screen.availableGeometry()
        x = global_center.x() - self.width() // 2
        y = global_center.y() - self.height() // 2
        x = min(max(x, available.left()), available.right() - self.width() + 1)
        y = min(max(y, available.top()), available.bottom() - self.height() + 1)
        self.move(x, y)
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.cycle_selection(-1)
        elif delta < 0:
            self.cycle_selection(1)
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Escape,):
            self.cancel()
        elif event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
        ):
            self.confirm_current()
        elif event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.cycle_selection(-1)
            event.accept()
            return
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self.cycle_selection(1)
            event.accept()
            return
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.cancel()
            event.accept()
            return
        super().mousePressEvent(event)
