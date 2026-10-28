import json
import re
import os
from datetime import date, datetime, timedelta
from pathlib import Path


EMOTION_OPTIONS = (
    {"label": "开心", "slug": "happy", "weight": 2.0},
    {"label": "嘲笑", "slug": "mock", "weight": 0.0},
    {"label": "焦虑", "slug": "anxious", "weight": -1.0},
    {"label": "惊讶", "slug": "surprised", "weight": 0.0},
    {"label": "意味深长", "slug": "meaningful", "weight": 1.0},
    {"label": "暴怒", "slug": "angry", "weight": -2.0},
    {"label": "无语", "slug": "speechless", "weight": -1.0},
)
EMOTION_LABELS = tuple(item["label"] for item in EMOTION_OPTIONS)
EMOTION_WEIGHTS = {item["label"]: item["weight"] for item in EMOTION_OPTIONS}


def _as_date(value):
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


class EmotionStore:
    def __init__(self, project_dir):
        self.project_dir = Path(project_dir)
        self.data_dir = self.project_dir / "data"
        self.data_file = self.data_dir / "emotion_history.json"
        self.legacy_dir = self.data_dir / "emotion_history"
        self.asset_dir = self.project_dir / "assets" / "图片" / "emotions"
        self._migrate_legacy_records()

    @staticmethod
    def _empty_database():
        return {"version": 1, "days": {}}

    @staticmethod
    def _normalize_counts(value):
        counts = {label: 0 for label in EMOTION_LABELS}
        stored = value if isinstance(value, dict) else {}
        for label in EMOTION_LABELS:
            try:
                counts[label] = max(0, int(stored.get(label, 0)))
            except (TypeError, ValueError):
                counts[label] = 0
        return counts

    def _read_database(self):
        if not self.data_file.is_file():
            return self._empty_database()
        try:
            payload = json.loads(self.data_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._empty_database()
        if not isinstance(payload, dict):
            return self._empty_database()
        days = payload.get("days", {})
        if not isinstance(days, dict):
            return self._empty_database()
        normalized_days = {
            str(day): self._normalize_counts(counts)
            for day, counts in days.items()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day))
        }
        return {"version": 1, "days": normalized_days}

    def _write_database(self, database):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.data_dir, 0o700)
        except OSError:
            pass
        temporary = self.data_file.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(database, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.data_file)

    def _migrate_legacy_records(self):
        if not self.legacy_dir.is_dir():
            if not self.data_file.exists():
                self._write_database(self._empty_database())
            return

        database = self._read_database()
        days = database["days"]
        for path in sorted(self.legacy_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            day = str(payload.get("date", path.stem))[:10]
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                continue
            incoming = self._normalize_counts(payload.get("counts", {}))
            current = days.get(day, {})
            days[day] = {
                label: max(int(current.get(label, 0)), incoming[label])
                for label in EMOTION_LABELS
            }

        self._write_database(database)
        for path in self.legacy_dir.glob("*.json"):
            path.unlink(missing_ok=True)
        try:
            self.legacy_dir.rmdir()
        except OSError:
            pass

    def asset_path(self, label):
        option = next(item for item in EMOTION_OPTIONS if item["label"] == label)
        return self.asset_dir / f"{option['slug']}.png"

    def load_day(self, day):
        selected = _as_date(day).isoformat()
        database = self._read_database()
        return {
            "date": selected,
            "counts": database["days"].get(selected, self._normalize_counts({})),
        }

    def record(self, label, on_date=None):
        if label not in EMOTION_LABELS:
            raise ValueError(f"未知情绪：{label}")
        selected = _as_date(on_date).isoformat()
        database = self._read_database()
        counts = database["days"].get(selected, self._normalize_counts({}))
        counts[label] += 1
        database["days"][selected] = counts
        self._write_database(database)
        return {
            "date": selected,
            "counts": counts,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def period_summary(self, start, end):
        start_date = _as_date(start)
        end_date = _as_date(end)
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        counts = {label: 0 for label in EMOTION_LABELS}
        cursor = start_date
        while cursor <= end_date:
            daily = self.load_day(cursor)["counts"]
            for label in EMOTION_LABELS:
                counts[label] += daily[label]
            cursor += timedelta(days=1)

        total = sum(counts.values())
        percentages = {
            label: round((counts[label] / total) * 100, 1) if total else 0.0
            for label in EMOTION_LABELS
        }
        if total:
            weighted = sum(
                counts[label] * EMOTION_WEIGHTS[label] for label in EMOTION_LABELS
            ) / total
            index = round(((weighted + 2.0) / 4.0) * 100)
            level = (
                "积极"
                if index >= 80
                else "平稳"
                if index >= 60
                else "波动"
                if index >= 40
                else "承压"
            )
        else:
            index = None
            level = "暂无记录"

        return {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "counts": counts,
            "percentages": percentages,
            "total": total,
            "index": index,
            "level": level,
        }

    def daily_summary_text(self, selected_label, on_date=None):
        today = self.load_day(on_date or date.today())["counts"]
        parts = [f"{label} {count}次" for label, count in today.items() if count]
        summary = "｜".join(parts) or "还没有情绪记录"
        if selected_label == "开心":
            lead = "这份开心我先收着，master。"
        elif selected_label in {"焦虑", "暴怒", "无语"}:
            lead = "记下了，别自己闷着，master。"
        elif selected_label == "意味深长":
            lead = "哦？这个表情有点意思，master。"
        else:
            lead = "记下了，master。"
        return f"{lead} 今天：{summary}"
