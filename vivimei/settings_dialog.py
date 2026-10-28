from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
)


from .oc import DeepSeekClient, DeepSeekError


class BalanceWorker(QThread):
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def run(self):
        try:
            self.succeeded.emit(DeepSeekClient().get_balance())
        except DeepSeekError as error:
            self.failed.emit(str(error))

class SettingsDialog(QDialog):
    def __init__(self, pet):
        super().__init__(pet)
        self.pet = pet
        self.setWindowTitle("维维美设置")
        self.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFixedWidth(360)
        self.balance_worker = None

        layout = QVBoxLayout(self)

        self.scale_label = QLabel(self)
        layout.addWidget(self.scale_label)

        self.scale_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.scale_slider.setObjectName("scaleSlider")
        self.scale_slider.setRange(20, 100)
        self.scale_slider.setSingleStep(5)
        self.scale_slider.setPageStep(5)
        self.scale_slider.setValue(round(self.pet.scale * 100))
        self.scale_slider.valueChanged.connect(self._scale_changed)
        layout.addWidget(self.scale_slider)

        self.keyboard_checkbox = QCheckBox("开启键盘控制移动（WASD）", self)
        self.keyboard_checkbox.setObjectName("keyboardCheckBox")
        self.keyboard_checkbox.setChecked(self.pet.keyboard_enabled)
        self.keyboard_checkbox.toggled.connect(self.pet.set_keyboard_control)
        layout.addWidget(self.keyboard_checkbox)

        self.free_activity_checkbox = QCheckBox("自由活动", self)
        self.free_activity_checkbox.setObjectName("freeActivityCheckBox")
        self.free_activity_checkbox.setChecked(self.pet.free_activity_enabled)
        self.free_activity_checkbox.toggled.connect(self.pet.set_free_activity)
        layout.addWidget(self.free_activity_checkbox)

        layout.addWidget(QLabel("master 的性别", self))
        self.gender_group = QButtonGroup(self)
        self.gender_radios = {
            "男": QRadioButton("男", self),
            "女": QRadioButton("女", self),
            "未知": QRadioButton("未知", self),
        }
        self.gender_radios["男"].setObjectName("genderMaleRadio")
        self.gender_radios["女"].setObjectName("genderFemaleRadio")
        self.gender_radios["未知"].setObjectName("genderUnknownRadio")
        for gender, radio in self.gender_radios.items():
            self.gender_group.addButton(radio)
            radio.toggled.connect(
                lambda checked, selected=gender:
                checked and self.pet.set_gender(selected)
            )
        gender_row = QHBoxLayout()
        for radio in self.gender_radios.values():
            gender_row.addWidget(radio)
        layout.addLayout(gender_row)
        self.gender_radios.get(self.pet.gender, self.gender_radios["未知"]).setChecked(True)

        layout.addWidget(QLabel("跌落设置", self))
        self.fall_group = QButtonGroup(self)
        self.fall_radios = {
            "smooth": QRadioButton("缓降飘落", self),
            "direct": QRadioButton("快速直落", self),
            "none": QRadioButton("固定屏幕", self),
        }
        self.fall_radios["smooth"].setObjectName("fallSmoothRadio")
        self.fall_radios["direct"].setObjectName("fallDirectRadio")
        self.fall_radios["none"].setObjectName("fallFixedRadio")
        for mode, radio in self.fall_radios.items():
            self.fall_group.addButton(radio)
            radio.toggled.connect(
                lambda checked, selected=mode:
                checked and self.pet.set_fall_mode(selected)
            )
        fall_row = QHBoxLayout()
        for radio in self.fall_radios.values():
            fall_row.addWidget(radio)
        layout.addLayout(fall_row)
        self.fall_radios[self.pet.fall_mode].setChecked(True)

        self.balance_label = QLabel("API 余额：查询中……", self)
        self.balance_label.setObjectName("apiBalanceLabel")
        layout.addWidget(self.balance_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("完成", self)
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self._update_scale_label(self.scale_slider.value())

    def showEvent(self, event):
        super().showEvent(event)
        self._load_balance()

    def _scale_changed(self, value):
        self.pet.set_scale(value / 100)
        self._update_scale_label(value)

    def _update_scale_label(self, value):
        self.scale_label.setText(f"自定义大小：{value}%")

    def _load_balance(self):
        client = DeepSeekClient()
        if not client.configured:
            self.balance_label.setText("API 余额：请先在 oc.py 的“API输入口”填写 Key")
            return

        if self.balance_worker is not None and self.balance_worker.isRunning():
            return

        self.balance_worker = BalanceWorker(self)
        self.balance_worker.succeeded.connect(
            lambda value: self.balance_label.setText(f"API 余额：{value}")
        )
        self.balance_worker.failed.connect(
            lambda message: self.balance_label.setText(f"API 余额：{message}")
        )
        self.balance_worker.start()
