from pathlib import Path

from PyQt6.QtCore import QFile, QIODevice
from PyQt6.QtCore import QObject, QPoint, QRectF, Qt, QUrl, QUrlQuery
from PyQt6.QtCore import pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QPainterPath, QRegion
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QVBoxLayout, QWidget


class WorkbenchBridge(QObject):
    closeRequested = pyqtSignal()
    tasksUpdated = pyqtSignal(str)
    reviewSummaryRequested = pyqtSignal(str, str, str, str)
    chatRequested = pyqtSignal(str, str)
    petSettingsRequested = pyqtSignal()

    @pyqtSlot()
    def close(self):
        self.closeRequested.emit()

    @pyqtSlot(str)
    def updateTasks(self, payload):
        self.tasksUpdated.emit(payload)

    @pyqtSlot(str, str, str, str)
    def requestReviewSummary(self, kind, start, end, payload):
        self.reviewSummaryRequested.emit(kind, start, end, payload)

    @pyqtSlot()
    def requestChatHistory(self):
        self.chatRequested.emit("history", "")

    @pyqtSlot(str)
    def sendChatMessage(self, text):
        self.chatRequested.emit("message", str(text or "").strip())

    @pyqtSlot()
    def requestPetSettings(self):
        self.petSettingsRequested.emit()

    @pyqtSlot()
    def startMove(self):
        window = self.parent()
        handle = window.windowHandle() if window is not None else None
        if handle is not None:
            handle.startSystemMove()


class WorkbenchWindow(QWidget):
    RESIZE_MARGIN = 8
    CORNER_RADIUS = 30

    def __init__(self, html_path, data_dir=None, parent=None):
        super().__init__(parent)
        self.html_path = Path(html_path)
        self.data_dir = Path(data_dir or self.html_path.parent.parent / "data" / "workbench_data")
        self._resize_edge = ""
        self._resize_start_geometry = None
        self._resize_start_global = QPoint()
        self._loaded_html_mtime_ns = None

        self.setWindowTitle("维维美｜生活工作台")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.profile = self._configure_profile()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)

        self.web_view = QWebEngineView(self)
        self.web_page = QWebEnginePage(self.profile, self.web_view)
        self.web_view.setPage(self.web_page)
        self.web_view.setStyleSheet("background: transparent;")
        self.web_view.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.web_view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalStorageEnabled, True
        )
        self.web_view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptEnabled, True
        )
        layout.addWidget(self.web_view)

        self.bridge = WorkbenchBridge(self)
        self.bridge.closeRequested.connect(self.hide)

        self.web_channel = QWebChannel(self.web_view.page())
        self.web_channel.registerObject("workbenchBridge", self.bridge)
        self.web_view.page().setWebChannel(self.web_channel)
        self._install_webchannel_script()
        self.web_view.loadFinished.connect(self._inject_bridge)

        self._set_initial_geometry()
        self._load_page()

    def _configure_profile(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        profile = QWebEngineProfile("vivimei-workbench", self)
        profile.setPersistentStoragePath(str(self.data_dir / "storage"))
        profile.setCachePath(str(self.data_dir / "cache"))
        return profile

    def _set_initial_geometry(self):
        screen = self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = max(620, available.width() // 2)
        height = max(430, available.height() // 2)
        width = min(width, available.width() - 40)
        height = min(height, available.height() - 40)
        x = available.left() + (available.width() - width) // 2
        y = available.top() + (available.height() - height) // 2
        self.setGeometry(x, y, width, height)
        self.setMinimumSize(580, 400)

    def _install_webchannel_script(self):
        resource = QFile(":/qtwebchannel/qwebchannel.js")
        if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
            return

        source = bytes(resource.readAll()).decode("utf-8")
        resource.close()

        script = QWebEngineScript()
        script.setName("qt-webchannel")
        script.setSourceCode(source)
        script.setInjectionPoint(
            QWebEngineScript.InjectionPoint.DocumentReady
        )
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        self.web_view.page().scripts().insert(script)

    def _load_page(self):
        if not self.html_path.is_file():
            return

        self._loaded_html_mtime_ns = self.html_path.stat().st_mtime_ns
        url = QUrl.fromLocalFile(str(self.html_path.resolve()))
        query = QUrlQuery(url)
        query.addQueryItem("embedded", "1")
        query.addQueryItem("build", str(self._loaded_html_mtime_ns))
        url.setQuery(query)
        self.web_view.load(url)

    def _inject_bridge(self, ok):
        if not ok:
            return

        self.web_view.page().runJavaScript(
            """
            (function () {
              document.documentElement.classList.add('embedded-workbench');
              document.body.classList.add('embedded-workbench');
                window.onReviewSummaryReady = function (kind, start, end, payload) {
                  window.dispatchEvent(new CustomEvent('review-summary-ready', {
                    detail: { kind, start, end, payload }
                  }));
                };
                window.onChatEvent = function (kind, payload) {
                  window.dispatchEvent(new CustomEvent('chat-event', {
                    detail: { kind, payload }
                  }));
                };
                window.onPetSettingsReady = function (payload) {
                  window.dispatchEvent(new CustomEvent('pet-settings-ready', {
                    detail: payload
                  }));
                };
              if (!window.qt || !qt.webChannelTransport) return;

              new QWebChannel(qt.webChannelTransport, function (channel) {
                window.workbenchBridge = channel.objects.workbenchBridge;
                let lastWorkbenchPayload = '';
                function syncWorkbenchTasks() {
                  const payload = localStorage.getItem('island_workbench_v2') || '{}';
                  if (payload === lastWorkbenchPayload) return;
                  lastWorkbenchPayload = payload;
                  if (window.workbenchBridge) window.workbenchBridge.updateTasks(payload);
                }
                window.__workbenchSyncTasks = syncWorkbenchTasks;
                syncWorkbenchTasks();
                window.dispatchEvent(new CustomEvent('workbench-bridge-ready'));
                if (window.__workbenchCloseBound) return;
                window.__workbenchCloseBound = true;
                document.addEventListener('click', function (event) {
                  const button = event.target.closest && event.target.closest('.close-btn');
                  if (!button) return;
                  event.preventDefault();
                  event.stopImmediatePropagation();
                  if (window.workbenchBridge) window.workbenchBridge.close();
                }, true);
                document.addEventListener('mousedown', function (event) {
                  if (event.button !== 0) return;
                  const header = event.target.closest && event.target.closest('.panel-head');
                  if (!header) return;
                  const control = event.target.closest('button, input, textarea, select, a');
                  if (control) return;
                  event.preventDefault();
                  if (window.workbenchBridge) window.workbenchBridge.startMove();
                }, true);
                window.setInterval(syncWorkbenchTasks, 1000);
              });
            })();
            """
        )

    def send_review_summary(self, kind, start, end, payload):
        import json

        self.web_view.page().runJavaScript(
            "window.onReviewSummaryReady && window.onReviewSummaryReady("
            f"{json.dumps(str(kind))}, {json.dumps(str(start))}, "
            f"{json.dumps(str(end))}, {json.dumps(payload, ensure_ascii=False)});"
        )

    def send_chat_event(self, kind, payload):
        import json

        self.web_view.page().runJavaScript(
            "window.onChatEvent && window.onChatEvent("
            f"{json.dumps(str(kind))}, {json.dumps(payload, ensure_ascii=False)});"
        )

    def send_pet_settings(self, payload):
        import json

        self.web_view.page().runJavaScript(
            "window.onPetSettingsReady && window.onPetSettingsReady("
            f"{json.dumps(payload, ensure_ascii=False)});"
        )

    def show_workbench(self):
        if not self.html_path.is_file():
            print("工作台页面不存在:", self.html_path)
            return
        if self._loaded_html_mtime_ns != self.html_path.stat().st_mtime_ns:
            self._load_page()
        self.show()
        self.raise_()
        self.activateWindow()
        self.web_view.setFocus()

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.show_workbench()

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()),
            self.CORNER_RADIUS,
            self.CORNER_RADIUS,
        )
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _edge_at(self, position):
        margin = self.RESIZE_MARGIN
        edges = []
        if position.x() <= margin:
            edges.append("left")
        elif position.x() >= self.width() - margin:
            edges.append("right")
        if position.y() <= margin:
            edges.append("top")
        elif position.y() >= self.height() - margin:
            edges.append("bottom")
        return "-".join(edges)

    def _cursor_for_edge(self, edge):
        if edge in {"left", "right"}:
            return Qt.CursorShape.SizeHorCursor
        if edge in {"top", "bottom"}:
            return Qt.CursorShape.SizeVerCursor
        if edge in {"left-top", "right-bottom"}:
            return Qt.CursorShape.SizeFDiagCursor
        if edge in {"right-top", "left-bottom"}:
            return Qt.CursorShape.SizeBDiagCursor
        return Qt.CursorShape.ArrowCursor

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            edge = self._edge_at(event.position().toPoint())
            if edge:
                self._resize_edge = edge
                self._resize_start_geometry = self.geometry()
                self._resize_start_global = event.globalPosition().toPoint()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_edge and event.buttons() & Qt.MouseButton.LeftButton:
            self._resize_window(event.globalPosition().toPoint())
            event.accept()
            return
        self.setCursor(self._cursor_for_edge(self._edge_at(event.position().toPoint())))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._resize_edge:
            self._resize_edge = ""
            self._resize_start_geometry = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _resize_window(self, global_position):
        if self._resize_start_geometry is None:
            return

        start = self._resize_start_geometry
        delta = global_position - self._resize_start_global
        left, top = start.left(), start.top()
        right, bottom = start.right(), start.bottom()
        minimum_width = self.minimumWidth()
        minimum_height = self.minimumHeight()

        if "left" in self._resize_edge:
            left = min(left + delta.x(), right - minimum_width)
        if "right" in self._resize_edge:
            right = max(right + delta.x(), left + minimum_width)
        if "top" in self._resize_edge:
            top = min(top + delta.y(), bottom - minimum_height)
        if "bottom" in self._resize_edge:
            bottom = max(bottom + delta.y(), top + minimum_height)

        self.setGeometry(left, top, right - left + 1, bottom - top + 1)
