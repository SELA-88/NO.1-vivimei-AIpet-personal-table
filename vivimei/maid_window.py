import ctypes
import json
import random
import sys
import time
from pathlib import Path

import yaml
from PyQt6.QtCore import QElapsedTimer, QObject, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtCore import QEasingCurve, QPropertyAnimation
from PyQt6.QtGui import QAction, QCursor, QImage, QMovie, QPainter, QPixmap
from PyQt6.QtGui import QTransform
from PyQt6.QtWidgets import QApplication, QLabel, QMenu, QWidget

from .emotion_store import EmotionStore
from .emotion_wheel import EmotionWheelOverlay


DEFAULT_SCALE = 0.35
MIN_SCALE = 0.20
MAX_SCALE = 1.00
EMOTION_LONG_PRESS_MS = 3000
KEYBOARD_STEP = 5
KEYBOARD_FLY_STEP = 4
KEYBOARD_INTERVAL_MS = 16
EDGE_REVEAL = 24
SYSTEM_IDLE_SECONDS = 5 * 60
FREE_MOVE_INTERVAL_MS = 30
FREE_MOVE_STEP = 3
FREE_SPEECH_LINES = (
    "你给路打油——",
    "哈基马路有——",
    "呀哈！",
    "哈基米",
    "哦南北路豆",
    "走两步，master。",
    "喝口水。",
)

CONTEXT_MENU_ITEMS = [
    ("设置", "settings"),
    ("关闭维维美", "quit"),
]

class MaidActions(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent

    def show_menu(self, global_pos):
        menu = QMenu(self.parent)
        for label, command in CONTEXT_MENU_ITEMS:
            item = QAction(label, menu)
            if command == "settings":
                item.triggered.connect(self.parent.open_settings)
            elif command == "quit":
                item.triggered.connect(self.parent.close)
            menu.addAction(item)
        menu.exec(global_pos)


def system_idle_seconds():
    if sys.platform != "darwin":
        return None

    try:
        framework = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        idle_function = framework.CGEventSourceSecondsSinceLastEventType
        idle_function.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        idle_function.restype = ctypes.c_double
        return float(idle_function(0, 0xFFFFFFFF))
    except (AttributeError, OSError):
        return None


class MaidWindow(QWidget):
    action_changed = pyqtSignal(str)
    double_clicked = pyqtSignal()
    chat_bubble_requested = pyqtSignal()
    speech_requested = pyqtSignal(str, int)
    emotion_selected = pyqtSignal(str)
    pet_settings_changed = pyqtSignal(str)

    def __init__(self, project_dir=None, settings_path=None):
        super().__init__()
        self.project_dir = Path(project_dir or Path(__file__).resolve().parent.parent)
        self.settings_path = Path(settings_path or self.project_dir / "data" / "settings.json")
        self.config_path = self._find_config()
        with self.config_path.open("r", encoding="utf-8") as file:
            self.anim_cfg = yaml.safe_load(file) or {}

        self.settings = self._load_settings()
        self.scale = min(MAX_SCALE, max(MIN_SCALE, float(self.settings["scale"])))
        self.keyboard_enabled = bool(self.settings["keyboard_control"])
        self.gender = str(self.settings["gender"])
        self.free_activity_enabled = bool(self.settings["free_activity"])
        self.fall_mode = str(self.settings["fall_mode"])
        self.keyboard_step = KEYBOARD_STEP
        self.floor_margin = int(self.anim_cfg.get("floor_margin", 20))
        self.system_idle_seconds = int(
            self.anim_cfg.get("system_idle_seconds", SYSTEM_IDLE_SECONDS)
        )

        self.current_action = ""
        self.current_movie = None
        self.native_size = QSize()
        self.current_loop = True
        self._drag_offset = None
        self._dragging = False
        self._suppress_click = False
        self._state = "auto"
        self._hidden_edge = None
        self._auto_hide_side = None
        self._auto_hide_outside_ticks = 0
        self._hidden_tail_rect = QRect()
        self._shown_position = None
        self._fall_velocity = 0.0
        self._fall_elapsed = QElapsedTimer()
        self._intro_replayed = False
        self._mirror_move = False
        self._keyboard_move_direction = 0
        self._keyboard_walk_action = None
        self._keyboard_left_pressed = False
        self._keyboard_right_pressed = False
        self._keyboard_fly_active = False
        self._keyboard_side_walk_direction = 0
        self._keyboard_side_flight_active = False
        self._keyboard_facing_left = None
        self._free_edge = None
        self._free_target = 0
        self._free_rest_cycles = 0
        self._next_free_fade_at = time.monotonic() + random.uniform(30, 120)
        self._next_free_speech_at = time.monotonic() + random.uniform(180, 600)
        self._free_transition_active = False
        self._free_fade_out = None
        self._free_fade_in = None
        self.hide_reveal = EDGE_REVEAL
        self._hide_bbox = None
        self.settings_dialog = None
        self.emotion_store = EmotionStore(self.project_dir)
        self._emotion_wheel = None
        self._emotion_press_active = False
        self._emotion_hold_ready = False
        self._emotion_press_global = QPoint()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("background: transparent;")
        self.resize(140, 140)
        self.label.resize(self.size())

        screen = self._screen_geometry()
        self.move(
            screen.left() + 90,
            screen.bottom() - self.height() - self.floor_margin,
        )

        self._finish_timer = QTimer(self)
        self._finish_timer.setSingleShot(True)
        self._finish_timer.timeout.connect(self._on_finished)

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._play_auto_idle_action)

        self._error_timer = QTimer(self)
        self._error_timer.setSingleShot(True)
        self._error_timer.timeout.connect(self.finish_loading)

        self._fall_timer = QTimer(self)
        self._fall_timer.setInterval(16)
        self._fall_timer.timeout.connect(self._step_fall)

        self._keyboard_move_timer = QTimer(self)
        self._keyboard_move_timer.setInterval(KEYBOARD_INTERVAL_MS)
        self._keyboard_move_timer.timeout.connect(self._on_keyboard_move_tick)

        self._free_move_timer = QTimer(self)
        self._free_move_timer.setInterval(FREE_MOVE_INTERVAL_MS)
        self._free_move_timer.timeout.connect(self._free_move_tick)

        self._free_pause_timer = QTimer(self)
        self._free_pause_timer.setSingleShot(True)
        self._free_pause_timer.timeout.connect(self._begin_free_walk)

        self._single_click_timer = QTimer(self)
        self._single_click_timer.setSingleShot(True)
        self._single_click_timer.setInterval(
            max(200, QApplication.doubleClickInterval())
        )
        self._single_click_timer.timeout.connect(self._play_pending_click_action)

        self._right_click_timer = QTimer(self)
        self._right_click_timer.setSingleShot(True)
        self._right_click_timer.setInterval(
            max(200, QApplication.doubleClickInterval())
        )
        self._right_click_timer.timeout.connect(self._show_context_menu)
        self._right_click_position = QPoint()
        self._suppress_right_click = False

        self._auto_hide_monitor = QTimer(self)
        self._auto_hide_monitor.setInterval(120)
        self._auto_hide_monitor.timeout.connect(self._check_hover_hide)

        self._idle_monitor = QTimer(self)
        self._idle_monitor.setInterval(5000)
        self._idle_monitor.timeout.connect(self._check_system_idle)
        self._idle_monitor.start()

        self._emotion_long_press_timer = QTimer(self)
        self._emotion_long_press_timer.setSingleShot(True)
        self._emotion_long_press_timer.setInterval(EMOTION_LONG_PRESS_MS)
        self._emotion_long_press_timer.timeout.connect(self._arm_emotion_wheel)

        self.actions = MaidActions(self)
        if not self.play_action("start", force_loop=False):
            self._play_idle()
        if self.free_activity_enabled:
            QTimer.singleShot(1000, self.start_free_activity)

    @property
    def is_dragging(self):
        return self._dragging

    @property
    def mirror_move(self):
        return self._mirror_move

    @property
    def keyboard_fly_active(self):
        return self._keyboard_fly_active

    def _load_settings(self):
        defaults = {
            "scale": DEFAULT_SCALE,
            "keyboard_control": False,
            "gender": "未知",
            "free_activity": False,
            "fall_mode": self.anim_cfg.get("fall_mode", "smooth"),
        }
        if not self.settings_path.exists():
            return defaults

        try:
            with self.settings_path.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
        except (OSError, ValueError):
            return defaults

        return {
            "scale": loaded.get("scale", DEFAULT_SCALE),
            "keyboard_control": loaded.get("keyboard_control", False),
            "gender": loaded.get("gender", "未知"),
            "free_activity": loaded.get("free_activity", False),
            "fall_mode": loaded.get(
                "fall_mode", self.anim_cfg.get("fall_mode", "smooth")
            ),
        }

    def _save_settings(self):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        with self.settings_path.open("w", encoding="utf-8") as file:
            json.dump(self.settings, file, ensure_ascii=False, indent=2)

    def _find_config(self):
        path = Path(__file__).with_name("maid_animations.yaml")
        if not path.exists():
            raise FileNotFoundError("找不到 maid_animations.yaml")
        return path

    def _find_asset_dir(self):
        path = self.project_dir / "assets" / "动作"
        if not path.is_dir():
            raise FileNotFoundError("找不到 assets/动作 文件夹")
        return path

    def _resolve_gif(self, action):
        action_text = str(action)
        if action_text.lower().endswith(".gif"):
            value = action_text
        else:
            actions = self.anim_cfg.get("actions", {})
            value = actions.get(action) or actions.get("idle")
        if not value:
            return None

        if "," in str(value):
            choices = [item.strip() for item in str(value).split(",") if item.strip()]
            value = random.choice(choices)

        path = self._find_asset_dir() / str(value).strip()
        return path if path.exists() else None

    def _screen_geometry(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return self.rect()
        return screen.availableGeometry()

    def _clamp_to_screen(self):
        if self._hidden_edge is not None:
            return

        screen = self._screen_geometry()
        maximum_x = screen.right() - self.width() + 1
        maximum_y = screen.bottom() - self.height() + 1
        x = min(max(self.x(), screen.left()), maximum_x)
        y = min(max(self.y(), screen.top()), maximum_y)
        self.move(x, y)

    def _resize_around_bottom_center(self, width, height):
        center_x = self.x() + self.width() // 2
        bottom = self.y() + self.height()
        self.resize(width, height)
        self.label.resize(width, height)
        self.move(center_x - width // 2, bottom - height)
        self._clamp_to_screen()

    def _resize_for_free_edge(self, width, height):
        if self._free_edge not in {"left", "right"}:
            self._resize_around_bottom_center(width, height)
            return

        screen = self._screen_geometry()
        center_y = self.y() + self.height() // 2
        window_width, window_height = height, width
        self.resize(window_width, window_height)
        self.label.resize(window_width, window_height)
        x = (
            screen.left()
            if self._free_edge == "left"
            else screen.right() - window_width + 1
        )
        y = min(
            screen.bottom() - window_height + 1,
            max(screen.top(), center_y - window_height // 2),
        )
        self.move(x, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.label.resize(event.size())

    def showEvent(self, event):
        super().showEvent(event)
        if not self._intro_replayed:
            self._intro_replayed = True
            QTimer.singleShot(0, self._play_intro)

    def _play_intro(self):
        if not self.play_action("start", force_loop=False):
            self._play_idle()

    def play_action(self, action, force_loop=None):
        path = self._resolve_gif(action)
        if path is None:
            if action != "idle":
                return self.play_action("idle", force_loop=True)
            return False

        loops = self.anim_cfg.get("loops", {})
        loop = loops.get(action, loops.get("idle", True))
        if force_loop is not None:
            loop = force_loop

        self._finish_timer.stop()
        if self.current_movie:
            try:
                self.current_movie.frameChanged.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.current_movie.stop()
            self.current_movie.deleteLater()

        movie = QMovie(str(path), parent=self)
        movie.setCacheMode(QMovie.CacheMode.CacheAll)
        if not movie.isValid():
            print("GIF 无法播放:", path)
            return False

        movie.jumpToFrame(0)
        native_size = movie.currentImage().size()
        if action == "hide":
            self._hide_bbox = self._alpha_bounding_box(movie.currentImage())
        duration_ms = self._movie_duration_ms(movie)
        movie.jumpToFrame(0)

        if not native_size.isEmpty():
            width = max(1, round(native_size.width() * self.scale))
            height = max(1, round(native_size.height() * self.scale))
            movie.setScaledSize(QSize(width, height))
            self._resize_for_free_edge(width, height)

        self.native_size = native_size
        self.current_action = action
        self.current_loop = bool(loop)
        self.current_movie = movie

        if not self.current_loop:
            self._finish_timer.start(duration_ms + 50)

        self.label.clear()
        movie.frameChanged.connect(
            lambda _frame_number, current=movie: self._render_movie_frame(current)
        )
        self._render_movie_frame(movie)
        movie.start()
        self.action_changed.emit(action)
        return True

    def _render_movie_frame(self, movie):
        if movie is not self.current_movie:
            return

        pixmap = movie.currentPixmap()
        if pixmap.isNull():
            return

        if self.current_action == "hide" and self._hidden_edge in {"left", "right"}:
            pixmap = self._align_hide_pixmap(pixmap)

        mirror_frame = False
        if self.current_action != "hide":
            if self._keyboard_facing_left is not None:
                mirror_frame = self._keyboard_facing_left
            elif self._free_edge == "bottom" and self.current_action in {"move", "sweat"}:
                mirror_frame = self._mirror_move
            elif self.current_action in {"move", "sweat"} and self._mirror_move:
                mirror_frame = True
            elif self._auto_hide_side == "right":
                mirror_frame = True
            else:
                mirror_frame = self._should_face_left()
            if self._free_edge in {"left", "right"}:
                mirror_frame = (
                    self._mirror_move
                    if self.current_action in {"move", "sweat"}
                    else False
                )
        if mirror_frame:
            pixmap = QPixmap.fromImage(pixmap.toImage().mirrored(True, False))
        if self._free_edge == "left":
            pixmap = pixmap.transformed(
                QTransform().rotate(90),
                Qt.TransformationMode.SmoothTransformation,
            )
        elif self._free_edge == "right":
            pixmap = pixmap.transformed(
                QTransform().rotate(-90),
                Qt.TransformationMode.SmoothTransformation,
            )

        scaled = pixmap.scaled(
            self.label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.label.setPixmap(scaled)

    def _should_face_left(self):
        screen = self._screen_geometry()
        window_center_x = self.x() + self.width() / 2
        return window_center_x > screen.center().x()

    @staticmethod
    def _alpha_bounding_box(image):
        xs = []
        ys = []
        for y in range(image.height()):
            for x in range(image.width()):
                if image.pixelColor(x, y).alpha() > 10:
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def _align_hide_pixmap(self, pixmap):
        if self._hide_bbox is None:
            return pixmap

        image = pixmap.toImage()
        left, _top, right, _bottom = self._hide_bbox

        if self._hidden_edge == "left":
            image = image.mirrored(True, False)
            mirrored_left = image.width() - 1 - right
            offset_x = left - mirrored_left
        else:
            offset_x = -left

        canvas = QImage(
            image.size(), QImage.Format.Format_ARGB32_Premultiplied
        )
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawImage(offset_x, 0, image)
        painter.end()
        return QPixmap.fromImage(canvas)

    @staticmethod
    def _movie_duration_ms(movie):
        if movie.frameCount() <= 0:
            return 0

        movie.jumpToFrame(0)
        total_ms = 0
        for frame_number in range(movie.frameCount()):
            total_ms += max(20, movie.nextFrameDelay())
            if frame_number < movie.frameCount() - 1:
                movie.jumpToNextFrame()
        return total_ms

    def _play_idle(self):
        self._state = "auto"
        self.play_action("idle", force_loop=True)
        self._schedule_idle()

    def _schedule_idle(self):
        self._idle_timer.stop()
        if self.free_activity_enabled:
            return
        if self._state != "auto":
            return

        idle_mode = self.anim_cfg.get("idle_mode", "default")
        ranges = {
            "default": (8, 18),
            "sport": (5, 12),
            "lazy": (12, 25),
        }
        minimum, maximum = ranges.get(idle_mode, ranges["default"])
        self._idle_timer.start(random.randint(minimum, maximum) * 1000)

    def _play_auto_idle_action(self):
        if self._state != "auto":
            return
        if self.free_activity_enabled:
            return

        idle_mode = self.anim_cfg.get("idle_mode", "default")
        choices = {
            "default": [
                ("idle", True),
                ("sit", True),
                ("sleep", True),
            ],
            "sport": [
                ("idle", True),
                ("move", True),
                ("fly", True),
                ("jump", False),
            ],
            "lazy": [
                ("idle", True),
                ("sit", True),
                ("sleep", True),
                ("sleep", True),
            ],
        }
        action, loop = random.choice(choices.get(idle_mode, choices["default"]))
        self.play_action(action, force_loop=loop)
        self._schedule_idle()

    def _choose_free_edge(self):
        return random.choices(
            ["bottom", "left", "right"],
            weights=(5, 3, 3),
            k=1,
        )[0]

    def _activate_free_edge(self, edge):
        if edge not in {"bottom", "left", "right"}:
            edge = "bottom"
        self._free_edge = edge
        self._auto_hide_side = None
        if self._hidden_edge is not None:
            self.show_from_hidden()

        width = max(1, round(self.native_size.width() * self.scale))
        height = max(1, round(self.native_size.height() * self.scale))
        self._resize_for_free_edge(width, height)
        screen = self._screen_geometry()
        if edge == "bottom":
            x = screen.center().x() - self.width() // 2
            y = screen.bottom() - self.height() + 1 - self.floor_margin
        elif edge == "left":
            x = screen.left()
            y = screen.center().y() - self.height() // 2
        else:
            x = screen.right() - self.width() + 1
            y = screen.center().y() - self.height() // 2
        self.move(
            min(max(x, screen.left()), screen.right() - self.width() + 1),
            min(max(y, screen.top()), screen.bottom() - self.height() + 1),
        )
        self._render_movie_frame(self.current_movie)

    def _fade_transition_to_edge(self, edge):
        if self._free_transition_active:
            return
        self._free_transition_active = True
        self._free_move_timer.stop()
        self._free_pause_timer.stop()
        self._free_fade_out = QPropertyAnimation(self, b"windowOpacity", self)
        self._free_fade_out.setDuration(520)
        self._free_fade_out.setStartValue(self.windowOpacity())
        self._free_fade_out.setEndValue(0.0)
        self._free_fade_out.setEasingCurve(QEasingCurve.Type.InOutQuad)

        def finish_fade_out():
            if not self.free_activity_enabled:
                self._free_transition_active = False
                self.setWindowOpacity(1.0)
                return
            self._activate_free_edge(edge)
            self.play_action("idle", force_loop=True)
            self._free_fade_in = QPropertyAnimation(self, b"windowOpacity", self)
            self._free_fade_in.setDuration(580)
            self._free_fade_in.setStartValue(0.0)
            self._free_fade_in.setEndValue(1.0)
            self._free_fade_in.setEasingCurve(QEasingCurve.Type.InOutQuad)

            def finish_fade_in():
                self._free_transition_active = False
                self.setWindowOpacity(1.0)
                self._next_free_fade_at = time.monotonic() + random.uniform(30, 120)
                self._free_pause_timer.start(800)

            self._free_fade_in.finished.connect(finish_fade_in)
            self._free_fade_in.start()

        self._free_fade_out.finished.connect(finish_fade_out)
        self._free_fade_out.start()

    def pause_free_activity(self, duration_ms=30000):
        if not self.free_activity_enabled:
            return
        self._free_move_timer.stop()
        self._free_pause_timer.stop()
        QTimer.singleShot(max(1000, int(duration_ms)), self.resume_free_activity)

    def resume_free_activity(self):
        if (
            not self.free_activity_enabled
            or self._dragging
            or self._hidden_edge is not None
            or self._state in {"system_sleep", "loading", "error"}
        ):
            return
        self.setWindowOpacity(1.0)
        self._free_transition_active = False
        if self._free_edge is None:
            self.start_free_activity()
            return
        self.play_action("idle", force_loop=True)
        self._free_pause_timer.start(random.randint(500, 1800))

    def start_free_activity(self):
        if not self.free_activity_enabled or self._dragging:
            return
        if self._free_transition_active or self._hidden_edge is not None:
            return
        if self._state in {"system_sleep", "loading", "error"}:
            return
        self._fall_timer.stop()
        self._idle_timer.stop()
        self._free_move_timer.stop()
        self._free_pause_timer.stop()
        self._fade_transition_to_edge(self._choose_free_edge())

    def _begin_free_walk(self):
        if (
            not self.free_activity_enabled
            or self._dragging
            or self._hidden_edge is not None
        ):
            return
        if self._free_edge is None:
            self._fade_transition_to_edge(self._choose_free_edge())
            return
        if time.monotonic() >= self._next_free_fade_at:
            choices = [edge for edge in ("bottom", "left", "right") if edge != self._free_edge]
            self._fade_transition_to_edge(random.choice(choices))
            return
        screen = self._screen_geometry()
        margin = 0
        if self._free_edge == "bottom":
            self._free_target = random.randint(
                screen.left() + margin,
                max(screen.left() + margin, screen.right() - self.width() - margin),
            )
            self._mirror_move = self._free_target < self.x()
        else:
            self._free_target = random.randint(
                screen.top() + margin,
                max(screen.top() + margin, screen.bottom() - self.height() - margin),
            )
            if self._free_edge == "left":
                self._mirror_move = self._free_target < self.y()
            else:
                self._mirror_move = self._free_target > self.y()
        self.play_action("move", force_loop=True)
        self._free_move_timer.start()

    def _free_move_tick(self):
        if not self.free_activity_enabled or self._dragging:
            self._free_move_timer.stop()
            return
        horizontal = self._free_edge == "bottom"
        current = self.x() if horizontal else self.y()
        if abs(self._free_target - current) <= FREE_MOVE_STEP:
            if horizontal:
                self.move(self._free_target, self.y())
            else:
                self.move(self.x(), self._free_target)
            self._finish_free_walk()
            return
        step = FREE_MOVE_STEP if self._free_target > current else -FREE_MOVE_STEP
        if horizontal:
            self.move(current + step, self.y())
        else:
            self.move(self.x(), current + step)

    def _finish_free_walk(self):
        self._free_move_timer.stop()
        if not self.free_activity_enabled:
            return
        now = time.monotonic()
        if now >= self._next_free_speech_at and random.random() < 0.65:
            self._next_free_speech_at = now + random.uniform(300, 900)
            self.play_action("idle", force_loop=True)
            self.speech_requested.emit(random.choice(FREE_SPEECH_LINES), 5000)
            self.pause_free_activity(10000)
            return
        action = random.choice(["idle", "sit", "sleep", "interact", "sweat", "jump"])
        self.play_action(
            action,
            force_loop=action in {"idle", "sit", "sleep", "interact", "sweat"},
        )
        self._free_pause_timer.start(random.randint(30000, 120000))

    def set_free_activity(self, enabled):
        self.free_activity_enabled = bool(enabled)
        self.settings["free_activity"] = self.free_activity_enabled
        self._save_settings()
        self._free_move_timer.stop()
        self._free_pause_timer.stop()
        if self._free_fade_out is not None:
            self._free_fade_out.stop()
        if self._free_fade_in is not None:
            self._free_fade_in.stop()
        self._free_transition_active = False
        self.setWindowOpacity(1.0)
        if self.free_activity_enabled:
            self.start_free_activity()
            return

        self._free_edge = None
        self._mirror_move = False
        self._play_idle()
        screen = self._screen_geometry()
        self.move(
            self.x(),
            screen.bottom() - self.height() + 1 - self.floor_margin,
        )
        self._clamp_to_screen()

    def set_gender(self, gender):
        value = str(gender or "未知")
        if value not in {"男", "女", "未知"}:
            value = "未知"
        self.gender = value
        self.settings["gender"] = value
        self._save_settings()
        self.pet_settings_changed.emit(value)

    def _on_finished(self):
        if self._state in {"loading", "error", "hidden"}:
            return
        self._play_idle()

    def show_loading(self):
        self._cancel_motion()
        self._state = "loading"
        self._idle_timer.stop()
        self.play_action("move", force_loop=True)

    def finish_loading(self):
        self._error_timer.stop()
        self._play_idle()

    def show_error(self):
        self._cancel_motion()
        self._state = "error"
        self._idle_timer.stop()
        self.play_action("sweat", force_loop=True)
        self._error_timer.start(3000)

    def _check_system_idle(self):
        idle_seconds = system_idle_seconds()
        if idle_seconds is None or self._hidden_edge is not None or self._dragging:
            return

        if idle_seconds >= self.system_idle_seconds and self._state == "auto":
            self._state = "system_sleep"
            self._idle_timer.stop()
            self._free_move_timer.stop()
            self._free_pause_timer.stop()
            self.play_action("sleep", force_loop=True)
        elif idle_seconds < self.system_idle_seconds and self._state == "system_sleep":
            if self.free_activity_enabled:
                QTimer.singleShot(500, self.resume_free_activity)
            else:
                self._play_idle()

    def _cancel_motion(self):
        self._finish_timer.stop()
        self._error_timer.stop()
        self._fall_timer.stop()
        self._idle_timer.stop()
        self._free_move_timer.stop()
        self._free_pause_timer.stop()
        self._stop_keyboard_motion()
        if self._state in {"system_sleep", "loading", "error"}:
            self._state = "auto"

    def start_fall(self, force_mode=None, from_keyboard=False):
        self._fall_timer.stop()
        mode = force_mode or self.fall_mode
        if from_keyboard and self.current_action != "fly":
            self.play_action("fly", force_loop=True)
        if mode == "none":
            self._play_idle()
            return

        screen = self._screen_geometry()
        floor_y = screen.bottom() - self.height() + 1 - self.floor_margin
        if self.y() >= floor_y:
            self.move(self.x(), floor_y)
            self._play_idle()
            return

        if mode == "direct":
            self._state = "falling"
            self._fall_velocity = random.uniform(260.0, 360.0)
            self._fall_elapsed.restart()
            self._fall_timer.start()
            return

        self._state = "falling"
        self._fall_velocity = 0.0
        self._fall_elapsed.restart()
        self._fall_timer.start()

    def _step_fall(self):
        screen = self._screen_geometry()
        floor_y = screen.bottom() - self.height() + 1 - self.floor_margin
        elapsed_seconds = max(0.001, self._fall_elapsed.restart() / 1000.0)

        if self.fall_mode == "direct":
            next_y = min(
                floor_y, round(self.y() + self._fall_velocity * elapsed_seconds)
            )
            self.move(self.x(), next_y)
            if next_y >= floor_y:
                self._fall_timer.stop()
                self._play_idle()
            return

        if self.anim_cfg.get("smooth_fall", True):
            self._fall_velocity = min(
                260.0, self._fall_velocity + 360.0 * elapsed_seconds
            )
        else:
            self._fall_velocity = 520.0

        next_y = min(floor_y, round(self.y() + self._fall_velocity * elapsed_seconds))
        self.move(self.x(), next_y)
        if next_y >= floor_y:
            self._fall_timer.stop()
            self._play_idle()

    def _hide_to_edge(self, edge):
        self._shown_position = QPoint(self.x(), self.y())
        self._free_move_timer.stop()
        self._free_pause_timer.stop()
        self._free_edge = None
        self._state = "hidden"
        self._hidden_edge = edge
        self._auto_hide_side = edge
        self.play_action("hide", force_loop=True)

        screen = self._screen_geometry()
        if self._hide_bbox is None:
            self.hide_reveal = EDGE_REVEAL
        else:
            tail_width = self._hide_bbox[2] - self._hide_bbox[0] + 1
            self.hide_reveal = max(
                EDGE_REVEAL, round(tail_width * self.scale) + 4
            )

        if edge == "left":
            hidden_x = screen.left() - self.width() + self.hide_reveal
        else:
            hidden_x = screen.right() - self.hide_reveal + 1

        self._idle_timer.stop()
        self.move(hidden_x, self.y())
        self._hidden_tail_rect = QRect(self.frameGeometry())
        self._auto_hide_monitor.start()
        self._auto_hide_outside_ticks = 0

    def show_from_hidden(self):
        if self._hidden_edge is None:
            return

        self._hidden_edge = None
        self._auto_hide_outside_ticks = 0
        self._auto_hide_monitor.start()
        self._play_idle()
        if self._shown_position is not None:
            self.move(self._shown_position)
            self._clamp_to_screen()
        if self.free_activity_enabled:
            QTimer.singleShot(30000, self.resume_free_activity)

    def _hover_zone_contains(self, global_position):
        return (
            self.frameGeometry().contains(global_position)
            or self._hidden_tail_rect.contains(global_position)
        )

    def _check_hover_hide(self):
        if not self._auto_hide_side:
            self._auto_hide_monitor.stop()
            return

        cursor_position = QCursor.pos()

        if self._hidden_edge is not None:
            if self._hidden_tail_rect.contains(cursor_position):
                self.show_from_hidden()
            return

        if self._dragging or self._hover_zone_contains(cursor_position):
            self._auto_hide_outside_ticks = 0
            return

        self._auto_hide_outside_ticks += 1
        if self._auto_hide_outside_ticks >= 4:
            self._hide_to_edge(self._auto_hide_side)

    def enterEvent(self, event):
        if self._hidden_edge is not None:
            self.show_from_hidden()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._auto_hide_side and self._hidden_edge is None:
            self._auto_hide_outside_ticks = 2
        super().leaveEvent(event)

    def set_scale(self, scale):
        self.scale = min(MAX_SCALE, max(MIN_SCALE, float(scale)))
        self.settings["scale"] = self.scale
        self._save_settings()
        if self.current_action:
            self.play_action(self.current_action, force_loop=self.current_loop)

    def set_keyboard_control(self, enabled):
        self.keyboard_enabled = bool(enabled)
        self.settings["keyboard_control"] = self.keyboard_enabled
        self._save_settings()
        if not self.keyboard_enabled:
            self._stop_keyboard_motion()

    def _stop_keyboard_motion(self):
        self._keyboard_move_timer.stop()
        self._keyboard_move_direction = 0
        self._keyboard_walk_action = None
        self._keyboard_left_pressed = False
        self._keyboard_right_pressed = False
        self._keyboard_fly_active = False
        self._keyboard_side_walk_direction = 0
        self._keyboard_side_flight_active = False
        self._keyboard_facing_left = None
        self._mirror_move = False

    def _leave_side_pose_for_keyboard(self):
        if self._free_edge not in {"left", "right"}:
            return
        self._free_edge = "bottom"
        self._mirror_move = False
        self._auto_hide_side = None
        width = max(1, round(self.native_size.width() * self.scale))
        height = max(1, round(self.native_size.height() * self.scale))
        self._resize_for_free_edge(width, height)
        if self.current_movie is not None:
            self._render_movie_frame(self.current_movie)

    def _start_side_walk(self, direction):
        if self._free_edge not in {"left", "right"} or direction == 0:
            return False
        edge = self._free_edge
        self._keyboard_side_walk_direction = direction
        self._keyboard_side_flight_active = False
        self._keyboard_fly_active = False
        self._keyboard_move_direction = 0
        self._keyboard_walk_action = "move"
        self._keyboard_facing_left = None
        self._mirror_move = direction < 0 if edge == "left" else direction > 0
        self.play_action(self._keyboard_walk_action, force_loop=True)
        moved = self._move_side_walk_step(direction)
        if not moved:
            self._stop_side_walk()
            return False
        if not self._keyboard_move_timer.isActive():
            self._keyboard_move_timer.start()
        return True

    def _move_side_walk_step(self, direction):
        if self._free_edge not in {"left", "right"}:
            return False
        screen = self._screen_geometry()
        new_y = min(
            screen.bottom() - self.height() + 1,
            max(screen.top(), self.y() + direction * KEYBOARD_STEP),
        )
        if new_y == self.y():
            return False
        self.move(self.x(), new_y)
        return True

    def _stop_side_walk(self):
        self._keyboard_side_walk_direction = 0
        self._keyboard_walk_action = None
        self._mirror_move = False
        if (
            not self._keyboard_side_flight_active
            and not self._keyboard_fly_active
            and self._keyboard_move_direction == 0
        ):
            self._keyboard_move_timer.stop()
        if self._free_edge in {"left", "right"} and self._state != "falling":
            self._play_idle()

    def _start_side_flight(self, direction):
        if self._free_edge not in {"left", "right"} or direction == 0:
            return False
        self._keyboard_side_walk_direction = 0
        self._keyboard_side_flight_active = True
        self._keyboard_fly_active = False
        self._keyboard_move_direction = direction
        self._keyboard_walk_action = None
        self._leave_side_pose_for_keyboard()
        self._keyboard_facing_left = direction < 0
        self._mirror_move = direction < 0
        if self.current_action != "fly":
            self.play_action("fly", force_loop=True)
        moved = self._move_side_flight_step(direction)
        if not moved and self._keyboard_side_flight_active:
            self._finish_side_flight()
        if self._keyboard_side_flight_active and not self._keyboard_move_timer.isActive():
            self._keyboard_move_timer.start()
        return moved

    def _move_side_flight_step(self, direction):
        screen = self._screen_geometry()
        new_x = self.x() + direction * KEYBOARD_FLY_STEP
        if direction < 0 and new_x <= screen.left():
            self._finish_side_flight("left")
            return False
        if direction > 0 and new_x >= screen.right() - self.width() + 1:
            self._finish_side_flight("right")
            return False
        new_x = min(
            screen.right() - self.width() + 1,
            max(screen.left(), new_x),
        )
        if new_x == self.x():
            return False
        self.move(new_x, self.y())
        return True

    def _finish_side_flight(self, edge=None):
        self._keyboard_side_flight_active = False
        self._keyboard_move_direction = 0
        self._keyboard_walk_action = None
        self._keyboard_facing_left = None
        self._mirror_move = False
        if edge in {"left", "right"}:
            self._snap_keyboard_to_edge(edge)
        self._keyboard_move_timer.stop()
        if self._state != "falling":
            self._play_idle()

    def _move_by_keyboard_step(self, direction):
        if direction == 0:
            return False

        self._free_move_timer.stop()
        self._free_pause_timer.stop()
        self._auto_hide_side = None

        expected_mirror = direction < 0
        self._keyboard_facing_left = expected_mirror
        mirror_changed = expected_mirror != self._mirror_move
        self._mirror_move = expected_mirror
        walk_action = self._keyboard_walk_action or "move"
        should_update_animation = (
            not self._keyboard_fly_active
            and self._state != "falling"
            and (self.current_action != walk_action or mirror_changed)
        )
        if should_update_animation:
            self.play_action(walk_action, force_loop=True)

        screen = self._screen_geometry()
        new_x = self.x() + direction * self.keyboard_step
        if direction < 0 and new_x <= screen.left():
            self._snap_keyboard_to_edge("left")
            return False
        if direction > 0 and new_x >= screen.right() - self.width() + 1:
            self._snap_keyboard_to_edge("right")
            return False
        new_x = min(
            screen.right() - self.width() + 1,
            max(screen.left(), new_x),
        )
        if new_x == self.x():
            return False

        self.move(new_x, self.y())
        return True

    def _snap_keyboard_to_edge(self, edge):
        self._free_edge = edge
        self._auto_hide_side = None
        width = max(1, round(self.native_size.width() * self.scale))
        height = max(1, round(self.native_size.height() * self.scale))
        self._resize_for_free_edge(width, height)
        screen = self._screen_geometry()
        x = screen.left() if edge == "left" else screen.right() - self.width() + 1
        y = min(
            screen.bottom() - self.height() + 1,
            max(screen.top(), self.y()),
        )
        self.move(x, y)
        self._render_movie_frame(self.current_movie)

    def _set_keyboard_move_direction(self, direction):
        if self._keyboard_side_flight_active:
            if direction == 0:
                self._finish_side_flight()
                return
            self._keyboard_move_direction = direction
            self._keyboard_facing_left = direction < 0
            self._mirror_move = direction < 0
            if self.current_action != "fly":
                self.play_action("fly", force_loop=True)
            if not self._move_side_flight_step(direction):
                if self._keyboard_side_flight_active:
                    self._finish_side_flight()
                return
            if not self._keyboard_move_timer.isActive():
                self._keyboard_move_timer.start()
            return

        if direction != 0 and self._free_edge in {"left", "right"}:
            edge = self._free_edge
            inward = (edge == "left" and direction > 0) or (
                edge == "right" and direction < 0
            )
            if inward:
                self._start_side_flight(direction)
            else:
                self._keyboard_move_direction = 0
                self._keyboard_walk_action = None
                self._mirror_move = False
                self._snap_keyboard_to_edge(edge)
                self._play_idle()
            return

        previous_direction = self._keyboard_move_direction
        self._keyboard_move_direction = direction
        if direction != 0 and direction != previous_direction:
            self._keyboard_walk_action = random.choice(["move", "sweat"])
        if direction == 0:
            if not self._keyboard_fly_active:
                self._keyboard_move_timer.stop()
            self._keyboard_walk_action = None
            self._mirror_move = False
            if (
                not self._dragging
                and self._state != "falling"
                and not self._keyboard_fly_active
            ):
                self._keyboard_facing_left = None
                self._play_idle()
            return

        self._move_by_keyboard_step(direction)
        if not self._keyboard_move_timer.isActive():
            self._keyboard_move_timer.start()

    def _sync_keyboard_direction(self):
        if self._keyboard_left_pressed and not self._keyboard_right_pressed:
            self._set_keyboard_move_direction(-1)
        elif self._keyboard_right_pressed and not self._keyboard_left_pressed:
            self._set_keyboard_move_direction(1)
        else:
            self._set_keyboard_move_direction(0)

    def _on_keyboard_move_tick(self):
        if not self.keyboard_enabled:
            self._keyboard_move_timer.stop()
            return

        if self._keyboard_side_walk_direction != 0:
            if not self._move_side_walk_step(self._keyboard_side_walk_direction):
                self._stop_side_walk()
            return

        if self._keyboard_side_flight_active:
            if not self._move_side_flight_step(self._keyboard_move_direction):
                if self._keyboard_side_flight_active:
                    self._finish_side_flight()
            return

        if self._keyboard_fly_active and not self._move_up_by_keyboard_step():
            self._keyboard_fly_active = False

        if self._keyboard_move_direction == 0:
            if not self._keyboard_fly_active:
                self._keyboard_move_timer.stop()
            return

        if not self._move_by_keyboard_step(self._keyboard_move_direction):
            self._keyboard_move_timer.stop()
            self._set_keyboard_move_direction(0)

    def _move_up_by_keyboard_step(self):
        if self._keyboard_facing_left is None:
            self._keyboard_facing_left = self._should_face_left()
        self._fall_timer.stop()
        if self._state == "falling":
            self._state = "auto"
        screen = self._screen_geometry()
        new_y = max(screen.top(), self.y() - KEYBOARD_FLY_STEP)
        if new_y == self.y():
            return False

        if self.current_action != "fly":
            self.play_action("fly", force_loop=True)

        self.move(self.x(), new_y)
        return True

    def set_fall_mode(self, mode):
        if mode not in {"smooth", "direct", "none"}:
            return
        self.fall_mode = mode
        self.settings["fall_mode"] = mode
        self._save_settings()

    def open_settings(self):
        from .settings_dialog import SettingsDialog

        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def _clear_suppress_click(self):
        self._suppress_click = False

    def _play_pending_click_action(self):
        click_pool = self.anim_cfg.get("click_pool", "interact.gif, die.gif")
        choices = [
            name.strip() for name in str(click_pool).split(",") if name.strip()
        ]
        if choices:
            self.play_action(random.choice(choices), force_loop=False)

    def _show_context_menu(self):
        if not self._suppress_right_click:
            self.actions.show_menu(self._right_click_position)

    def _clear_suppress_right_click(self):
        self._suppress_right_click = False

    def _arm_emotion_wheel(self):
        if self._emotion_press_active and not self._dragging and self._hidden_edge is None:
            self._emotion_hold_ready = True

    def _show_emotion_wheel(self):
        if (
            not self._emotion_press_active
            or not self._emotion_hold_ready
            or self._dragging
            or self._hidden_edge is not None
        ):
            return
        self._single_click_timer.stop()
        self._suppress_click = True
        self._emotion_hold_ready = False
        self._free_move_timer.stop()
        self._free_pause_timer.stop()
        if self._emotion_wheel is not None:
            self._emotion_wheel.close()
            self._emotion_wheel = None
        wheel = EmotionWheelOverlay(self.emotion_store)
        wheel.emotionSelected.connect(self._handle_emotion_selected)
        wheel.cancelled.connect(self._handle_emotion_cancelled)
        self._emotion_wheel = wheel
        wheel.show_at(self.mapToGlobal(self.rect().center()))
        if self.free_activity_enabled:
            self.pause_free_activity(15000)

    def _handle_emotion_selected(self, label):
        self.emotion_store.record(label)
        self.emotion_selected.emit(label)
        self.speech_requested.emit(self.emotion_store.daily_summary_text(label), 4500)
        self._emotion_hold_ready = False
        self._suppress_click = False
        if self._emotion_wheel is not None:
            self._emotion_wheel.deleteLater()
            self._emotion_wheel = None
        if self.free_activity_enabled:
            QTimer.singleShot(30000, self.resume_free_activity)

    def _handle_emotion_cancelled(self):
        self._emotion_hold_ready = False
        self._suppress_click = False
        if self._emotion_wheel is not None:
            self._emotion_wheel.deleteLater()
            self._emotion_wheel = None
        if self.free_activity_enabled:
            QTimer.singleShot(30000, self.resume_free_activity)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._keyboard_facing_left = None
            self._single_click_timer.stop()
            if self._hidden_edge is not None:
                self.show_from_hidden()

            self._cancel_motion()
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            self._dragging = False
            self.activateWindow()
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._emotion_press_active = True
            self._emotion_hold_ready = False
            self._emotion_press_global = event.globalPosition().toPoint()
            self._emotion_long_press_timer.start()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._right_click_position = event.globalPosition().toPoint()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._emotion_press_active:
            distance = (
                event.globalPosition().toPoint() - self._emotion_press_global
            ).manhattanLength()
            if distance > QApplication.startDragDistance():
                self._emotion_long_press_timer.stop()
                self._emotion_press_active = False
                self._emotion_hold_ready = False
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if not self._dragging:
                self._dragging = True
                self.play_action("fly", force_loop=True)
                self._drag_offset = (
                    event.globalPosition().toPoint()
                    - self.frameGeometry().topLeft()
                )

            new_position = event.globalPosition().toPoint() - self._drag_offset
            self.move(new_position)
            self._clamp_to_screen()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._emotion_long_press_timer.stop()
            self._emotion_press_active = False
            self._emotion_hold_ready = False
            if self._emotion_wheel is not None and self._emotion_wheel.isVisible():
                self._drag_offset = None
                self._dragging = False
                event.accept()
                return
            if self._dragging:
                self._dragging = False
                self._drag_offset = None
                screen = self._screen_geometry()
                if self.x() <= screen.left() + 30:
                    self._hide_to_edge("left")
                elif self.x() + self.width() >= screen.right() - 30:
                    self._hide_to_edge("right")
                else:
                    self._auto_hide_side = None
                    self.start_fall()
            elif not self._suppress_click:
                self._single_click_timer.start()

            self._drag_offset = None
            self._dragging = False
            self._suppress_click = False
            if self.free_activity_enabled and self._hidden_edge is None:
                QTimer.singleShot(30000, self.resume_free_activity)
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            if not self._suppress_right_click:
                self._right_click_position = event.globalPosition().toPoint()
                self._right_click_timer.start()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if (
            self._emotion_press_active
            and self._emotion_hold_ready
            and event.buttons() & Qt.MouseButton.LeftButton
            and self._emotion_wheel is None
        ):
            self._show_emotion_wheel()
            event.accept()
            return
        super().wheelEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._emotion_long_press_timer.stop()
            self._emotion_press_active = False
            self._emotion_hold_ready = False
            self._single_click_timer.stop()
            self._suppress_click = True
            QTimer.singleShot(250, self._clear_suppress_click)
            self.double_clicked.emit()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self._right_click_timer.stop()
            self._suppress_right_click = True
            QTimer.singleShot(300, self._clear_suppress_right_click)
            if self.free_activity_enabled:
                self.pause_free_activity(15000)
            self.chat_bubble_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if not self.keyboard_enabled:
            super().keyPressEvent(event)
            return

        self._free_move_timer.stop()
        self._free_pause_timer.stop()
        if event.isAutoRepeat():
            event.accept()
            return

        key = event.key()
        side_key_handled = False
        if self._free_edge in {"left", "right"} and key in (
            Qt.Key.Key_W,
            Qt.Key.Key_Up,
            Qt.Key.Key_Space,
        ):
            self._start_side_walk(-1)
            side_key_handled = True
        elif self._free_edge in {"left", "right"} and key in (
            Qt.Key.Key_S,
            Qt.Key.Key_Down,
        ):
            self._start_side_walk(1)
            side_key_handled = True
        if side_key_handled:
            event.accept()
            return
        if not side_key_handled and key in (Qt.Key.Key_A, Qt.Key.Key_Left):
            self._keyboard_left_pressed = True
            self._sync_keyboard_direction()
        elif key in (Qt.Key.Key_D, Qt.Key.Key_Right):
            self._keyboard_right_pressed = True
            self._sync_keyboard_direction()
        elif key in (Qt.Key.Key_W, Qt.Key.Key_Up, Qt.Key.Key_Space):
            self._keyboard_fly_active = True
            self._move_up_by_keyboard_step()
            if not self._keyboard_move_timer.isActive():
                self._keyboard_move_timer.start()
        elif key in (Qt.Key.Key_S, Qt.Key.Key_Down):
            if self._keyboard_facing_left is None:
                self._keyboard_facing_left = self._should_face_left()
            self.start_fall(force_mode="direct", from_keyboard=True)
        elif key == Qt.Key.Key_Escape:
            self.set_keyboard_control(False)
            self._play_idle()
        else:
            super().keyPressEvent(event)
            return

        event.accept()

    def keyReleaseEvent(self, event):
        if not self.keyboard_enabled or event.isAutoRepeat():
            super().keyReleaseEvent(event)
            return

        if self.free_activity_enabled:
            QTimer.singleShot(30000, self.resume_free_activity)
        key = event.key()
        if self._free_edge in {"left", "right"} and key in (
            Qt.Key.Key_W,
            Qt.Key.Key_Up,
            Qt.Key.Key_Space,
            Qt.Key.Key_S,
            Qt.Key.Key_Down,
        ):
            self._stop_side_walk()
            event.accept()
            return
        if key in (Qt.Key.Key_A, Qt.Key.Key_Left):
            self._keyboard_left_pressed = False
            self._sync_keyboard_direction()
        elif key in (Qt.Key.Key_D, Qt.Key.Key_Right):
            self._keyboard_right_pressed = False
            self._sync_keyboard_direction()
        elif key in (Qt.Key.Key_W, Qt.Key.Key_Up, Qt.Key.Key_Space):
            self._keyboard_fly_active = False
            screen = self._screen_geometry()
            at_bottom = self.y() + self.height() >= (
                screen.bottom() - self.floor_margin + 1
            )
            if not at_bottom and self.fall_mode != "none":
                self.start_fall()
            elif self._keyboard_move_direction == 0:
                self._play_idle()
        else:
            super().keyReleaseEvent(event)
            return

        event.accept()

    def closeEvent(self, event):
        self._idle_monitor.stop()
        self._idle_timer.stop()
        self._finish_timer.stop()
        self._error_timer.stop()
        self._fall_timer.stop()
        self._keyboard_move_timer.stop()
        self._free_move_timer.stop()
        self._free_pause_timer.stop()
        self._single_click_timer.stop()
        self._emotion_long_press_timer.stop()
        if self._emotion_wheel is not None:
            self._emotion_wheel.close()
            self._emotion_wheel = None
        super().closeEvent(event)
