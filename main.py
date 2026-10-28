import sys
import fcntl
import os
import base64
import json

from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice, QThread, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QImage
from PyQt6.QtWidgets import QApplication, QMessageBox
from vivimei.chat_window import AIWorker, SimpleChatBubble
from vivimei.maid_window import MaidWindow, system_idle_seconds
from vivimei.oc import PetAI
from vivimei.reminders import WorkbenchReminderController
from vivimei.workbench_window import WorkbenchWindow


class SingleInstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self._file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return False
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        self._file = lock_file
        return True

    def release(self):
        if self._file is None:
            return
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None


class ReviewSummaryWorker(QThread):
    succeeded = pyqtSignal(str, str, str, object)
    failed = pyqtSignal(str, str, str, str)

    def __init__(self, pet_ai, kind, start, end, parent=None):
        super().__init__(parent)
        self.pet_ai = pet_ai
        self.kind = str(kind)
        self.period_start = str(start)
        self.period_end = str(end)

    def run(self):
        try:
            if self.kind == "week":
                result = self.pet_ai.summarize_week(self.period_start, self.period_end)
            elif self.kind == "month":
                year, month = self.period_start[:7].split("-", 1)
                result = self.pet_ai.summarize_month_summary(year, month)
            else:
                raise ValueError(f"未知复盘类型：{self.kind}")
            if result.get("error"):
                self.failed.emit(self.kind, self.period_start, self.period_end, str(result["error"]))
            else:
                self.succeeded.emit(self.kind, self.period_start, self.period_end, result)
        except Exception as error:
            self.failed.emit(
                self.kind,
                self.period_start,
                self.period_end,
                f"复盘记录生成失败：{error}",
            )


app = QApplication(sys.argv)
app.setApplicationName("维维美")
app.setApplicationDisplayName("维维美")
project_dir = Path(__file__).resolve().parent
instance_lock = SingleInstanceLock(project_dir / ".vivimei.lock")
if not instance_lock.acquire():
    QMessageBox.information(
        None,
        "维维美",
        "维维美已经在运行。\n请在屏幕上找一下，或先右键选择“关闭维维美”再重新启动。",
    )
    sys.exit(0)
app.aboutToQuit.connect(instance_lock.release)
icon = QIcon(str(project_dir / "assets" / "app_icon.icns"))
app.setWindowIcon(icon)
pet = MaidWindow()
pet.setWindowIcon(icon)
workbench = WorkbenchWindow(
    pet.project_dir / "workbench" / "index.html",
    data_dir=pet.project_dir / "data" / "workbench_data",
)
pet.double_clicked.connect(workbench.show_workbench)
pet_ai = PetAI(pet.project_dir)
chat_bubble = SimpleChatBubble(pet_ai)
pet.chat_bubble_requested.connect(lambda: chat_bubble.show_for_pet(pet))
reminders = WorkbenchReminderController(
    idle_seconds_provider=system_idle_seconds,
)
workbench.bridge.tasksUpdated.connect(reminders.handle_payload)
workbench.bridge.tasksUpdated.connect(pet_ai.update_workbench)
review_workers = []
chat_workers = []
avatar_cache = {}


def start_review_summary(kind, start, end, payload):
    pet_ai.update_workbench(payload)
    worker = ReviewSummaryWorker(pet_ai, kind, start, end)
    review_workers.append(worker)

    def cleanup():
        if worker in review_workers:
            review_workers.remove(worker)
        worker.deleteLater()

    worker.succeeded.connect(workbench.send_review_summary)
    worker.failed.connect(
        lambda kind, start, end, message: workbench.send_review_summary(
            kind, start, end, {"error": message}
        )
    )
    worker.finished.connect(cleanup)
    worker.start()

workbench.bridge.reviewSummaryRequested.connect(start_review_summary)

def avatar_data_url(avatar_path, role="assistant"):
    if not avatar_path and role == "user":
        avatar_path = project_dir / "assets" / "图片" / "user_cat.png"
    path = Path(avatar_path) if avatar_path else None
    if path is not None and not path.is_absolute():
        path = project_dir / path
    if path is None or not path.is_file():
        return ""
    try:
        resolved = path.resolve()
        resolved.relative_to(project_dir.resolve())
    except (OSError, ValueError):
        return ""
    cache_key = (str(resolved), resolved.stat().st_mtime_ns)
    if cache_key in avatar_cache:
        return avatar_cache[cache_key]
    image = QImage(str(resolved))
    if image.isNull():
        return ""
    side = min(image.width(), image.height())
    image = image.copy(
        (image.width() - side) // 2,
        (image.height() - side) // 2,
        side,
        side,
    ).scaled(
        96,
        96,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    encoded = base64.b64encode(bytes(buffer.data())).decode("ascii")
    url = "data:" + "image/png;base64," + encoded
    avatar_cache[cache_key] = url
    return url


def chat_history_payload():
    messages = []
    for item in pet_ai.store.load_today():
        role = item.get("role", "assistant")
        messages.append(
            {
                "role": role,
                "content": item.get("content", ""),
                "avatar_url": avatar_data_url(item.get("avatar", ""), role),
                "time": item.get("time", ""),
            }
        )
    return {"messages": messages}


def handle_chat_request(kind, payload):
    if kind == "history":
        workbench.send_chat_event("history", chat_history_payload())
        return
    if kind != "message":
        return

    text = str(payload or "").strip()
    if not text:
        return
    pet_ai.store.add("user", text)
    worker = AIWorker(pet_ai, text, record_user=False)
    chat_workers.append(worker)

    def cleanup():
        if worker in chat_workers:
            chat_workers.remove(worker)
        worker.deleteLater()

    def reply_ready(answer, avatar):
        pet.pause_free_activity(10000)
        workbench.send_chat_event(
            "reply",
            {
                "answer": answer,
                "avatar_url": avatar_data_url(avatar),
                "time": __import__("datetime").datetime.now().strftime("%H:%M"),
            },
        )

    def reply_failed(message):
        workbench.send_chat_event("error", {"message": f"……{message}"})

    worker.succeeded.connect(reply_ready)
    worker.failed.connect(reply_failed)
    worker.finished.connect(cleanup)
    worker.start()


workbench.bridge.chatRequested.connect(handle_chat_request)

def send_pet_settings():
    workbench.send_pet_settings({"gender": pet.gender})

workbench.bridge.petSettingsRequested.connect(send_pet_settings)
pet.pet_settings_changed.connect(lambda _gender: send_pet_settings())

def show_pet_bubble(text, duration):
    pet.pause_free_activity(max(5000, duration + 2000))
    chat_bubble.show_reminder(pet, text, duration)

pet.speech_requested.connect(show_pet_bubble)
reminders.bubbleRequested.connect(
    show_pet_bubble
)
pet.show()
QTimer.singleShot(1000, reminders.on_app_started)
app.exec()
