import json
import random
from datetime import date, datetime, timedelta

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class WorkbenchReminderController(QObject):
    bubbleRequested = pyqtSignal(str, int)

    WORK_START_HOUR = 9
    WORK_END_HOUR = 18
    EVENING_START_HOUR = 20
    EVENING_END_HOUR = 23
    REST_IDLE_SECONDS = 120

    MORNING_GREETINGS = (
        "早呀，master。今天也别把自己逼太紧，先醒醒神。",
        "早上好，master。我在这儿，先喝口水再开工，别一坐下就失联。",
        "嗯？这么早就来了，master。行吧，我陪你慢慢启动。",
    )
    SEDENTARY_REMINDERS = (
        "master，坐得够久了。站起来走两步，别把自己焊在椅子上。",
        "起来，master。倒杯水，顺手看看远处，眼睛和腰都在抗议。",
        "该歇一下了，master。去窗边站会儿，回来再跟事情较劲。",
    )
    EVENING_WIND_DOWNS = (
        "master，今天辛苦了。剩下的明天再收拾，先让自己喘口气。",
        "晚上了，master。辛苦一天了，做不完的不会长腿跑掉，先去吃点东西。",
        "收工时间到了，master。今天辛苦到现在已经够了，别再硬撑。",
    )
    NEW_TASK_MESSAGES = (
        "记下了，master。新清单“{title}”归我盯梢了。",
        "哦？“{title}”？行，master 想做的事，我记一笔。",
        "“{title}”已上岛。别到时候问我为什么没提醒你呀，master。",
    )
    COMPLETED_TASK_MESSAGES = (
        "不错嘛，master。“{title}”清掉了。",
        "“{title}”清掉了。嗯，今天还算有点样子，master。",
        "完成得挺利索嘛，master。“{title}”这条清掉了。",
    )
    TASK_START_MESSAGES = (
        "到点了，master。“{title}”该开场了，别让计划只活在清单里。",
        "master，时间到了。“{title}”现在不碰，晚点可别怪我没提醒。",
        "该开工了，master。“{title}”已经在等你，别装没看见。",
    )
    TASK_END_MESSAGES = (
        "master，“{title}”到结束时间了。做完就收尾，没做完也别装死。",
        "时间到了，master。“{title}”还没打勾，你打算让它自己完成？",
        "“{title}”已经到点了，master。要么收尾，要么老实延期。",
    )
    OVERDUE_TASK_MESSAGES = (
        "master，过期未完成的清单还挂着，“{title}”可不会自己消失。",
        "提醒你一下，master。“{title}”已经逾期，还要继续装看不见？",
        "“{title}”拖到现在还没做完，master。今天挑它开刀，还是继续留着碍眼？",
    )
    DDL_MESSAGES = (
        "master，“{title}”{urgency}就到截止时间了，别装没看见。",
        "提醒一下，master。“{title}”{urgency}就到期限，别等它自己消失。",
        "{urgency}截止的是“{title}”，master。你最好现在就有点动作。",
    )
    WEEKLY_REVIEW_MESSAGES = (
        "master，周复盘还没写哦。别光让事情过去，随便记两句。",
        "周日了，master。复盘还空着，真打算把这一周糊弄过去？",
        "该写周复盘了，master。哪怕只记几句，也比装作没发生过强。",
    )
    REVIEW_FINISHED_MESSAGES = (
        "这周的复盘也写完了呀，master。行，至少没偷懒。",
        "复盘已落笔，master。总算知道回头看一眼了。",
        "不错，master。周复盘写完了，这一周勉强算有始有终。",
    )

    def __init__(
        self,
        parent=None,
        *,
        now_provider=None,
        idle_seconds_provider=None,
        sedentary_interval_range=(45 * 60, 75 * 60),
    ):
        super().__init__(parent)
        self.tasks = []
        self.reviews = []
        self.initialized = False
        self.reminded = set()
        self._now = now_provider or datetime.now
        self._idle_seconds = idle_seconds_provider or (lambda: None)
        self._random = random.SystemRandom()

        low, high = sorted(
            (int(sedentary_interval_range[0]), int(sedentary_interval_range[1]))
        )
        self._sedentary_interval_range = (max(1, low), max(1, high))
        self._next_sedentary_seconds = self._new_sedentary_target()
        self._active_seconds = 0.0
        self._last_check_at = self._now()

        self.timer = QTimer(self)
        self.timer.setInterval(30_000)
        self.timer.timeout.connect(self.check_reminders)
        self.timer.start()

    def _choose(self, choices):
        return self._random.choice(tuple(choices))

    def _new_sedentary_target(self):
        low, high = self._sedentary_interval_range
        return self._random.uniform(low, high)

    def on_app_started(self):
        now = self._now()
        if not 5 <= now.hour < 12:
            return
        key = ("morning_greeting", now.date().isoformat())
        if key in self.reminded:
            return
        self.reminded.add(key)
        self.bubbleRequested.emit(self._choose(self.MORNING_GREETINGS), 5000)

    def handle_payload(self, payload):
        try:
            data = json.loads(payload or "{}")
        except ValueError:
            return

        new_tasks = data.get("todos", [])
        new_reviews = data.get("reviews", [])
        if not isinstance(new_tasks, list):
            new_tasks = []
        if not isinstance(new_reviews, list):
            new_reviews = []

        if self.initialized:
            old_by_id = {item.get("id"): item for item in self.tasks}
            for task in new_tasks:
                task_id = task.get("id")
                old_task = old_by_id.get(task_id)
                title = task.get("title", "未命名")
                if old_task is None:
                    self.bubbleRequested.emit(
                        self._choose(self.NEW_TASK_MESSAGES).format(title=title),
                        3000,
                    )
                elif (
                    old_task.get("status") != "已完成"
                    and task.get("status") == "已完成"
                ):
                    self.bubbleRequested.emit(
                        self._choose(self.COMPLETED_TASK_MESSAGES).format(title=title),
                        3000,
                    )

            if len(new_reviews) > len(self.reviews):
                self.bubbleRequested.emit(
                    self._choose(self.REVIEW_FINISHED_MESSAGES),
                    3000,
                )

        self.tasks = new_tasks
        self.reviews = new_reviews
        self.initialized = True
        self.check_reminders()

    @staticmethod
    def _parse_date(value):
        try:
            return date.fromisoformat(str(value)[:10])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_datetime(value):
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
            return parsed.replace(tzinfo=None)
        except ValueError:
            return None

    def _is_work_time(self, moment):
        return self.WORK_START_HOUR <= moment.hour < self.WORK_END_HOUR

    def _track_active_time(self, now):
        previous = self._last_check_at
        self._last_check_at = now
        if not self._is_work_time(now):
            self._active_seconds = 0.0
            return

        idle_seconds = self._idle_seconds()
        if idle_seconds is not None and idle_seconds >= self.REST_IDLE_SECONDS:
            self._active_seconds = 0.0
            self._next_sedentary_seconds = self._new_sedentary_target()
            return

        if previous.date() == now.date() and self._is_work_time(previous):
            elapsed = max(0.0, (now - previous).total_seconds())
            self._active_seconds += min(elapsed, 2 * 60 * 60)
        elif previous.date() == now.date():
            work_start = now.replace(
                hour=self.WORK_START_HOUR,
                minute=0,
                second=0,
                microsecond=0,
            )
            self._active_seconds += min(
                max(0.0, (now - work_start).total_seconds()),
                2 * 60 * 60,
            )

    def check_reminders(self):
        if not self.initialized:
            return

        now = self._now()
        today = now.date()
        self._track_active_time(now)

        for task in self.tasks:
            if task.get("status") == "已完成":
                continue
            task_id = task.get("id")
            title = task.get("title", "清单")
            start_at = self._parse_datetime(task.get("start"))
            end_at = self._parse_datetime(task.get("end"))

            if start_at and start_at <= now <= start_at + timedelta(minutes=30):
                key = (today.isoformat(), task_id, "task_start", start_at.isoformat())
                if key not in self.reminded:
                    self.reminded.add(key)
                    self.bubbleRequested.emit(
                        self._choose(self.TASK_START_MESSAGES).format(title=title),
                        4000,
                    )
                    return

            if end_at and end_at <= now <= end_at + timedelta(minutes=15):
                key = (today.isoformat(), task_id, "task_end", end_at.isoformat())
                if key not in self.reminded:
                    self.reminded.add(key)
                    self.bubbleRequested.emit(
                        self._choose(self.TASK_END_MESSAGES).format(title=title),
                        5000,
                    )
                    return

        overdue_tasks = [
            task
            for task in self.tasks
            if task.get("status") != "已完成"
            and self._parse_date(task.get("end") or task.get("start"))
            and self._parse_date(task.get("end") or task.get("start")) < today
        ]
        if overdue_tasks:
            if now.hour >= 20:
                overdue_slot = "20"
            elif now.hour >= 9:
                overdue_slot = "09"
            else:
                overdue_slot = ""
            overdue_key = (today.isoformat(), "overdue_tasks", overdue_slot)
            if overdue_slot and overdue_key not in self.reminded:
                self.reminded.add(overdue_key)
                priority_order = {"紧急": 0, "重要": 1, "普通": 2}
                overdue_tasks.sort(
                    key=lambda task: (
                        priority_order.get(task.get("priority", "普通"), 2),
                        str(task.get("end", "")),
                    )
                )
                message = self._choose(self.OVERDUE_TASK_MESSAGES).format(
                    title=overdue_tasks[0].get("title", "清单")
                )
                if len(overdue_tasks) > 1:
                    message += f" 现在共有 {len(overdue_tasks)} 项逾期任务。"
                self.bubbleRequested.emit(message, 6000)
                return

        for task in self.tasks:
            if task.get("status") == "已完成":
                continue
            due = self._parse_date(task.get("end") or task.get("start"))
            if due is None:
                continue
            days_left = (due - today).days
            if days_left not in {3, 1}:
                continue

            if now.hour >= 20:
                slot = "20"
            elif now.hour >= 9:
                slot = "09"
            else:
                continue

            key = (today.isoformat(), task.get("id"), slot, days_left)
            if key in self.reminded:
                continue
            self.reminded.add(key)
            urgency = "明天" if days_left == 1 else "三天内"
            self.bubbleRequested.emit(
                self._choose(self.DDL_MESSAGES).format(
                    title=task.get("title", "清单"),
                    urgency=urgency,
                ),
                5000,
            )
            return

        if self._emit_weekly_review(now):
            return

        evening_key = ("evening_wind_down", today.isoformat())
        if (
            self.EVENING_START_HOUR <= now.hour < self.EVENING_END_HOUR
            and evening_key not in self.reminded
        ):
            self.reminded.add(evening_key)
            self.bubbleRequested.emit(self._choose(self.EVENING_WIND_DOWNS), 5000)
            return

        if (
            self._is_work_time(now)
            and self._active_seconds >= self._next_sedentary_seconds
        ):
            self._active_seconds = 0.0
            self._next_sedentary_seconds = self._new_sedentary_target()
            self.bubbleRequested.emit(self._choose(self.SEDENTARY_REMINDERS), 5000)

    def _emit_weekly_review(self, now):
        if now.weekday() != 6 or now.hour < 20:
            return False

        today = now.date()
        week_start = today - timedelta(days=today.weekday())
        week_key = ("weekly_review", week_start.isoformat())
        if week_key in self.reminded:
            return False

        has_review = False
        for review in self.reviews:
            start = self._parse_date(review.get("start"))
            end = self._parse_date(review.get("end")) or start
            if start and end and start <= today <= end:
                has_review = True
                break

        if has_review:
            return False

        self.reminded.add(week_key)
        self.bubbleRequested.emit(self._choose(self.WEEKLY_REVIEW_MESSAGES), 5000)
        return True
