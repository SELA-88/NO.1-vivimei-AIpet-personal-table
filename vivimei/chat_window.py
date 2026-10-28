import re

from PyQt6.QtCore import QPropertyAnimation, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics, QTextBlockFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizeGrip,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .oc import DeepSeekError, PetAI


class AIWorker(QThread):
    succeeded = pyqtSignal(str, str)
    failed = pyqtSignal(str)

    def __init__(self, pet_ai, question, record_user=True, parent=None):
        super().__init__(parent)
        self.pet_ai = pet_ai
        self.question = question
        self.record_user = record_user

    def run(self):
        try:
            answer, avatar = self.pet_ai.ask(
                self.question, record_user=self.record_user
            )
            self.succeeded.emit(answer, avatar)
        except DeepSeekError as error:
            self.failed.emit(str(error))
        except Exception as error:
            self.failed.emit(f"请求失败：{error}")


class ChatInput(QTextEdit):
    submitRequested = pyqtSignal()
    doubleClicked = pyqtSignal()
    interactionStarted = pyqtSignal()
    interactionFinished = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.submitRequested.emit()
                event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.interactionStarted.emit()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.interactionFinished.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class SimpleChatBubble(QWidget):
    chatRoomRequested = pyqtSignal()
    MIN_WIDTH = 280
    COMPACT_MIN_WIDTH = 150
    MAX_WIDTH = 460

    def __init__(self, pet_ai, parent=None):
        super().__init__(parent)
        self.resize(self.MIN_WIDTH, 72)
        self.pet_ai = pet_ai
        self.pet = None
        self.worker = None
        self._answer_text = ""
        self._type_index = 0
        self._holding = False
        self._hide_delay_override = None
        self._compact_mode = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame = QWidget(self)
        self.frame.setObjectName("bubbleFrame")
        outer.addWidget(self.frame)

        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self.icon_label = QLabel("🔍", self.frame)
        self.close_button = QPushButton("×", self.frame)
        self.close_button.setObjectName("bubbleClose")
        self.close_button.setFixedSize(22, 22)
        self.close_button.clicked.connect(self.hide)
        top.addWidget(self.icon_label)
        top.addStretch(1)
        top.addWidget(self.close_button)
        layout.addLayout(top)

        self.editor = ChatInput(self.frame)
        self.editor.setObjectName("bubbleEditor")
        self.editor.setFrameShape(QTextEdit.Shape.NoFrame)
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor.document().setDocumentMargin(0)
        self.editor.submitRequested.connect(self._submit)
        self.editor.doubleClicked.connect(self.chatRoomRequested.emit)
        self.editor.interactionStarted.connect(self._hold_started)
        self.editor.interactionFinished.connect(self._hold_finished)
        self.editor.textChanged.connect(self._adjust_height)
        layout.addWidget(self.editor)

        self.size_grip = QSizeGrip(self.frame)
        self.size_grip.setFixedSize(12, 12)
        self.size_grip.raise_()

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self._fade_out)

        self.type_timer = QTimer(self)
        # 对应 animal-island-ui-vue Typewriter 组件的默认 speed=90。
        self.type_timer.setInterval(90)
        self.type_timer.timeout.connect(self._type_next)

        self.fade_animation = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade_animation.setDuration(700)
        self.fade_animation.finished.connect(self.hide)

        self.setStyleSheet(
            """
            QWidget#bubbleFrame {
                background: #fffaf0;
                border: 2px solid #c4b89e;
                border-radius: 24px;
            }
            QLabel { color: #794f27; font-size: 15px; }
            QTextEdit#bubbleEditor {
                background: transparent;
                border: none;
                color: #794f27;
                font-size: 15px;
                selection-background-color: #e9d7aa;
            }
            QPushButton#bubbleClose {
                border: none;
                background: transparent;
                color: #c4b89e;
                font-size: 16px;
            }
            QPushButton#bubbleClose:hover { color: #794f27; }
            """
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.size_grip.move(self.width() - 18, self.height() - 18)

    def show_for_pet(self, pet):
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, False)
        self._compact_mode = False
        self.pet = pet
        self._hide_delay_override = None
        self.worker = None
        self._answer_text = ""
        self._type_index = 0
        self.editor.setReadOnly(False)
        self.editor.clear()
        self.editor.setPlaceholderText(
            __import__('random').choice(['干嘛……', '问啥子？', '搞么子？'])
        )
        self.icon_label.setText("🔍")
        self.setWindowOpacity(1.0)
        self._adjust_height()
        self._position_near_pet()
        self.show()
        self.raise_()
        self.activateWindow()
        self.editor.setFocus()

    def _position_near_pet(self):
        if self.pet is None:
            return
        pet_screen = self.pet.screen()
        screen = (
            pet_screen.availableGeometry()
            if pet_screen is not None
            else QApplication.primaryScreen().availableGeometry()
        )
        width = self.width()
        height = max(self.height(), 56)
        pet_center = self.pet.frameGeometry().center()
        pet_top = self.pet.y()
        pet_bottom = self.pet.y() + self.pet.height()
        room_below = screen.bottom() - pet_bottom
        room_above = pet_top - screen.top()

        if room_below >= height + 8:
            # 常规情况：与桌宠并排，垂直居中。
            if pet_center.x() > screen.center().x():
                x = self.pet.x() - width - 4
            else:
                x = self.pet.x() + self.pet.width() + 4
            y = pet_center.y() - height // 2
        elif room_above >= height + 8:
            # 靠近屏幕底部：居中放在桌宠头顶。
            x = pet_center.x() - width // 2
            y = pet_top - height - 6
        else:
            x = pet_center.x() - width // 2
            y = pet_bottom + 6
        x = min(max(x, screen.left() + 4), screen.right() - width - 4)
        y = min(max(y, screen.top() + 4), screen.bottom() - height - 4)
        self.move(x, y)

    def _apply_line_spacing(self, text):
        normalized = re.sub(r"\n\s*\n+", "\n", str(text))
        self.editor.setPlainText(normalized)
        cursor = self.editor.textCursor()
        cursor.setPosition(0)
        cursor.movePosition(
            QTextCursor.MoveOperation.End,
            QTextCursor.MoveMode.KeepAnchor,
        )
        block_format = QTextBlockFormat()
        block_format.setLineHeight(
            110, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value
        )
        cursor.mergeBlockFormat(block_format)
        cursor.clearSelection()
        cursor.setPosition(len(normalized))
        self.editor.setTextCursor(cursor)

    def _dynamic_width(self):
        text = self.editor.toPlainText()
        minimum = self.COMPACT_MIN_WIDTH if self._compact_mode else self.MIN_WIDTH
        if not text:
            return minimum
        metrics = QFontMetrics(self.editor.font())
        pixels = max(
            (metrics.horizontalAdvance(line) for line in text.splitlines()),
            default=0,
        )
        return min(self.MAX_WIDTH, max(minimum, pixels + 54))

    def _adjust_height(self):
        if self.pet is None:
            max_height = 180
        else:
            max_height = max(120, min(260, round(self.pet.height() * 1.8)))
        document_height = int(self.editor.document().size().height()) + 54
        width = self._dynamic_width()
        self.setFixedHeight(max(56, min(max_height, document_height)))
        self.setFixedWidth(width)
        self.resize(width, self.height())
        if self.isVisible() and self.pet is not None:
            self._position_near_pet()

    def _submit(self):
        text = self.editor.toPlainText().strip()
        if not text or self.worker is not None:
            return

        self.hide_timer.stop()
        self._hide_delay_override = None
        self.fade_animation.stop()
        self.pet_ai.store.add("user", text)
        self.editor.setReadOnly(True)
        self.editor.setPlainText(text)
        self.icon_label.setText("💡")
        self._answer_text = "正在想……"
        self._type_index = len(self._answer_text)
        self._adjust_height()

        self.worker = AIWorker(
            self.pet_ai,
            text,
            record_user=False,
            parent=self,
        )
        self.worker.succeeded.connect(self._answer_ready)
        self.worker.failed.connect(self._answer_failed)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def show_reminder(self, pet, text, duration=5000):
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self._compact_mode = True
        self.pet = pet
        self.worker = None
        self._answer_text = str(text)
        self._type_index = 0
        self._hide_delay_override = duration
        self.setWindowOpacity(1.0)
        self.icon_label.setText("💡")
        self.editor.setReadOnly(True)
        self.editor.clear()
        self._adjust_height()
        self._position_near_pet()
        self.show()
        self.raise_()
        self.type_timer.start()

    def _answer_ready(self, answer, _avatar):
        self._answer_text = answer
        self._type_index = 0
        self.editor.clear()
        self.type_timer.start()

    def _answer_failed(self, message):
        self._answer_text = message
        self._type_index = 0
        self.editor.clear()
        self.type_timer.start()

    def _worker_finished(self):
        self.worker.deleteLater()
        self.worker = None

    def _type_next(self):
        if self._type_index >= len(self._answer_text):
            self.type_timer.stop()
            if not self._holding:
                delay = self._hide_delay_override or (
                    3000 if len(self._answer_text) <= 80 else 5000
                )
                self.hide_timer.start(delay)
            return
        self._type_index += 1
        self._apply_line_spacing(self._answer_text[: self._type_index])
        self._adjust_height()

    def _hold_started(self):
        self._holding = True
        self.hide_timer.stop()
        self.fade_animation.stop()

    def _hold_finished(self):
        self._holding = False
        if self._answer_text and self._type_index >= len(self._answer_text):
            self.hide_timer.start(3000)

    def _fade_out(self):
        self.fade_animation.stop()
        self.fade_animation.setStartValue(self.windowOpacity())
        self.fade_animation.setEndValue(0.0)
        self.fade_animation.start()
