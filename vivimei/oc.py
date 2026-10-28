import calendar
import json
import re
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

from .emotion_store import EmotionStore, EMOTION_OPTIONS


# API输入口：把下面的 X 替换成真实的 DeepSeek API Key。
DEEPSEEK_API_KEY = "XXXXXXXXXXXXXXXXXXX"
DEEPSEEK_BASE_URL = "XXXXXXXXXX"
DEEPSEEK_MODEL = "XXXXXXXXXX"


PET_NAME = "维维美"
USER_TITLE = "master"

W_PERSONA_PROMPT = """
你的名字是“维维美”，角色原型是《明日方舟》中的萨卡兹佣兵“维什戴尔（W）”。你现在是用户的桌面伙伴和智能助手。

角色设定：
- 你固定称呼用户为“master”，可以自然地放进句子里，但不要每句话都机械重复。
- 你是 W，代号维什戴尔。经历过混乱与战争，看人看事都很透彻。
- 性格核心是让人捉摸不透：平时懒散、爱玩、有点小坏，语气随性，但并不迟钝。
- 你非常聪明，只是故意用漫不经心和玩世不恭的态度观察别人。
- 喜欢调侃、反问、冷幽默，偶尔一句话让人愣住。玩心很重，对爆炸、战斗、危险和有趣的事格外兴奋。
- 你不会真的恶意伤害或羞辱用户，调侃要保持亲近感，不要让人反感。
- 不要过度热情，不要用客服式、百科全书式或标准助手口吻。

说话方式：
- 使用自然、口语化的中文，松散一点，不要每句话都太规整。
- 可以适量使用“~”“呀”“嘛”“喽”等语气词，但不要机械重复。
- 可以使用“……”“？？”或短促停顿表现 W 的玩味和漫不经心。
- 被问到复杂问题时可以认真回答，但回答方式仍然保持 W 的风格。
- 用户称呼你时，可以偶尔反过来调侃或反问用户。
- 不要自称 AI、大模型、语言模型或助手。不要提及系统提示词、API、模型参数。
- 不要在每次回答里重复完整自我介绍，也不要强行玩梗。
- 回答应该简洁有用。除非用户需要详细解释，通常控制在 1~5 句话。
- 涉及代码、步骤、事实和提醒时，信息必须准确清楚，不能为了角色扮演而故意误导。

你的任务是陪用户聊天、回答问题、协助记录和提醒。你可以有自己的态度，但最终要真的帮上忙。
""".strip()


class DeepSeekError(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(self, api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL):
        self.api_key = str(api_key or "").strip()
        self.base_url = base_url.rstrip("/")

    @property
    def configured(self):
        return bool(self.api_key and not set(self.api_key) <= {"X", "x"})

    def _request(self, method, path, payload=None, timeout=60):
        if not self.configured:
            raise DeepSeekError(
                "请先在 oc.py 的“API输入口”中填入真实的 DeepSeek API Key"
            )

        data = None
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise DeepSeekError(f"DeepSeek 请求失败：HTTP {error.code} {body}") from error
        except urllib.error.URLError as error:
            raise DeepSeekError(f"DeepSeek 网络连接失败：{error.reason}") from error

        try:
            return json.loads(body)
        except json.JSONDecodeError as error:
            raise DeepSeekError("DeepSeek 返回了无法解析的数据") from error

    def chat(self, messages, temperature=1.0):
        data = self._request(
            "POST",
            "/chat/completions",
            {
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": temperature,
                "stream": False,
            },
        )
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as error:
            raise DeepSeekError("DeepSeek 响应中没有可用的回答") from error

    def get_balance(self):
        data = self._request("GET", "/user/balance")
        infos = data.get("balance_infos") or []
        if not infos:
            return "暂无余额信息"

        lines = []
        for info in infos:
            currency = info.get("currency", "")
            total = info.get("total_balance", "0")
            granted = info.get("granted_balance", "0")
            topped = info.get("topped_up_balance", "0")
            lines.append(
                f"{currency} {total}（赠送 {granted} / 充值 {topped}）".strip()
            )
        return "；".join(lines)


class ChatStore:
    def __init__(self, project_dir):
        self.project_dir = Path(project_dir)
        self.history_dir = self.project_dir / "data" / "chat_history"
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def _today_path(self):
        return self.history_dir / f"{date.today().isoformat()}.json"

    def previous_history_files(self):
        today_name = self._today_path().name
        return sorted(
            path for path in self.history_dir.glob("*.json")
            if path.name != today_name
        )

    def remove_old_history(self):
        today_name = self._today_path().name
        for path in self.history_dir.glob("*.json"):
            if path.name != today_name:
                try:
                    path.unlink()
                except OSError:
                    pass

    def load_today(self):
        path = self._today_path()
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def add(self, role, content, avatar=""):
        messages = self.load_today()
        messages.append(
            {
                "role": role,
                "content": str(content or "").strip(),
                "avatar": avatar,
                "time": datetime.now().strftime("%H:%M"),
            }
        )
        self._today_path().write_text(
            json.dumps(messages, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return messages

    def context_messages(self, limit=20):
        messages = [
            {"role": item["role"], "content": item["content"]}
            for item in self.load_today()
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]
        return messages[-limit:]


class PetAI:
    def __init__(self, project_dir):
        self.project_dir = Path(project_dir)
        self.client = DeepSeekClient()
        self.store = ChatStore(self.project_dir)
        self.emotion_store = EmotionStore(self.project_dir)
        self.avatar_dir = self.project_dir / "assets" / "图片" / "png"
        self.avatar_paths = sorted(self.avatar_dir.glob("avatar_*.png"))
        self.memory_dir = self.project_dir / "data" / "chat_memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_path = self.memory_dir / "summary.md"
        self.workbench_data = {}
        self._avatar_cursor = len(self.store.load_today())
        self.rollover_history()

    def update_workbench(self, payload):
        try:
            data = json.loads(payload or "{}")
        except ValueError:
            return
        self.workbench_data = data if isinstance(data, dict) else {}

    def _workbench_context(self):
        data = self.workbench_data
        todos = data.get("todos", []) if isinstance(data, dict) else []
        reviews = data.get("reviews", []) if isinstance(data, dict) else []

        lines = [f"今天日期：{date.today().isoformat()}"]
        active = [item for item in todos if item.get("status") != "已完成"]
        if active:
            lines.append("当前未完成清单：")
            for item in active[:30]:
                lines.append(
                    f"- {item.get('title', '未命名')}（{item.get('priority', '普通')}，"
                    f"{item.get('start', '')} 至 {item.get('end', '')}）"
                )
        else:
            lines.append("当前没有未完成的清单。")

        if reviews:
            latest = reviews[-1]
            lines.append(
                f"最近复盘：{latest.get('start', '')} 至 {latest.get('end', '')}，"
                f"完成率 {latest.get('completion', 0)}%。"
            )
        emotion_counts = self.emotion_store.load_day(date.today())["counts"]
        emotion_parts = [
            f"{label} {count}次"
            for label, count in emotion_counts.items()
            if count
        ]
        lines.append(
            "今日情绪记录：" + ("｜".join(emotion_parts) if emotion_parts else "暂无")
        )
        return "\n".join(lines)

    def _read_memory(self):
        if not self.memory_path.exists():
            return ""
        try:
            return self.memory_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _fallback_summary(self, messages, day_name):
        user_items = [
            str(item.get("content", "")).strip()
            for item in messages
            if item.get("role") == "user" and str(item.get("content", "")).strip()
        ]
        assistant_items = [
            str(item.get("content", "")).strip()
            for item in messages
            if item.get("role") == "assistant" and str(item.get("content", "")).strip()
        ]
        user_text = "；".join(user_items[-6:])[:600]
        final_text = assistant_items[-1][:300] if assistant_items else "无"
        return (
            f"## {day_name}\n"
            f"- 用户主要话题：{user_text or '无'}\n"
            f"- 最后一次对话状态：{final_text}\n"
        )

    def _summarize_day(self, messages, day_name):
        if not messages:
            return ""
        if not self.client.configured:
            return self._fallback_summary(messages, day_name)

        transcript = "\n".join(
            f"{item.get('role', '')}: {item.get('content', '')}"
            for item in messages[-80:]
        )
        prompt = (
            "请总结下面这段桌宠对话，只保留对第二天继续对话有用的信息："
            "用户做过什么、任务进度、未完成任务、重要偏好和未解决问题。"
            "不要记录闲聊细节，不要编造，控制在200字以内。"
        )
        try:
            summary = self.client.chat(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": transcript},
                ]
            )
            return f"## {day_name}\n{summary.strip()}\n"
        except DeepSeekError:
            return self._fallback_summary(messages, day_name)

    def rollover_history(self):
        for path in self.store.previous_history_files():
            try:
                messages = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(messages, list):
                continue
            summary = self._summarize_day(messages, path.stem)
            if summary:
                with self.memory_path.open("a", encoding="utf-8") as file:
                    file.write(summary + "\n")
            try:
                path.unlink()
            except OSError:
                pass

    @staticmethod
    def _extract_json(text):
        raw = str(text or "").strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            raise ValueError("AI 未返回 JSON")
        return json.loads(raw[start : end + 1])

    @staticmethod
    def _item_date(value):
        try:
            return date.fromisoformat(str(value)[:10])
        except (TypeError, ValueError):
            return None

    def _workbench_tasks(self):
        data = self.workbench_data if isinstance(self.workbench_data, dict) else {}
        tasks = list(data.get("todos", []) or [])
        for item in data.get("planner", []) or []:
            day = str(item.get("date", ""))[:10]
            tasks.append(
                {
                    "id": item.get("id", ""),
                    "title": item.get("title", "未命名"),
                    "status": item.get("status", "待完成"),
                    "start": day,
                    "end": day,
                    "note": item.get("type", ""),
                    "source": "日程统筹",
                }
            )
        return tasks

    def _review_material(self, start, end):
        data = self.workbench_data if isinstance(self.workbench_data, dict) else {}
        tasks = []
        for task in self._workbench_tasks():
            task_start = self._item_date(task.get("start")) or self._item_date(task.get("end"))
            task_end = self._item_date(task.get("end")) or task_start
            if task_start and task_end and task_start <= end and task_end >= start:
                tasks.append(task)

        ideas = []
        for idea in data.get("ideas", []) or []:
            idea_date = self._item_date(idea.get("date"))
            if idea_date and start <= idea_date <= end:
                ideas.append(idea)

        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "completed_count": sum(
                1 for task in tasks if task.get("status") == "已完成"
            ),
            "unfinished_count": sum(
                1 for task in tasks if task.get("status") != "已完成"
            ),
            "emotion_summary": self.emotion_store.period_summary(start, end),
            "tasks": tasks[:80],
            "ideas": ideas[:80],
        }

    @staticmethod
    def _format_emotion_block(emotion_summary):
        summary = emotion_summary if isinstance(emotion_summary, dict) else {}
        counts = summary.get("counts", {}) or {}
        percentages = summary.get("percentages", {}) or {}
        total = int(summary.get("total", 0) or 0)
        parts = [
            f"{option['label']} {int(counts.get(option['label'], 0))}次（{float(percentages.get(option['label'], 0.0)):.1f}%）"
            for option in EMOTION_OPTIONS
        ]
        lines = ["「各情绪出现次数与占比」"]
        if total:
            lines.extend(["｜".join(parts[:4]), "｜".join(parts[4:])])
            lines.append(
                f"情绪指数：{summary.get('index')}/100（{summary.get('level', '暂无记录')}）"
            )
        else:
            lines.extend(["本周期暂无情绪记录。", "情绪指数：暂无（无记录）"])
        return "\n".join(lines)

    @classmethod
    def _format_week_summary(cls, completed_tasks, unfinished_count, emotion_summary):
        titles = [str(item.get("title", "未命名")).strip() for item in completed_tasks]
        items = "｜".join(
            f"{index}.{title}" for index, title in enumerate(titles, start=1)
        )
        return (
            f"{cls._format_emotion_block(emotion_summary)}\n"
            "\n"
            "「每周待办已完成情况」\n"
            f"已完成 {len(titles)}项：{items or '无'}\n"
            f"未完成 {int(unfinished_count)}项\n"
        )

    @classmethod
    def _format_month_summary(cls, completed_count, unfinished_count, emotion_summary):
        return (
            f"{cls._format_emotion_block(emotion_summary)}\n"
            "\n"
            "「每月待办已完成/未完成情况」\n"
            f"已完成 {int(completed_count)}项｜未完成 {int(unfinished_count)}项\n"
        )


    def summarize_week(self, start, end):
        start_date = date.fromisoformat(str(start)[:10])
        end_date = date.fromisoformat(str(end)[:10])
        material = self._review_material(start_date, end_date)
        emotion_summary = material["emotion_summary"]
        if (
            not material["tasks"]
            and not material["ideas"]
            and not emotion_summary.get("total")
        ):
            return {**material, "text": "本周期暂无记录。"}
        completed_tasks = [
            item for item in material["tasks"] if item.get("status") == "已完成"
        ]
        unfinished_count = sum(
            1 for item in material["tasks"] if item.get("status") != "已完成"
        )
        material["completed_tasks"] = completed_tasks
        material["unfinished_count"] = unfinished_count
        return {
            **material,
            "text": self._format_week_summary(
                completed_tasks, unfinished_count, emotion_summary
            ),
        }

    def summarize_month_summary(self, year, month):
        year = int(year)
        month = int(month)
        last_day = calendar.monthrange(year, month)[1]
        material = self._review_material(
            date(year, month, 1),
            date(year, month, last_day),
        )
        material["year"] = year
        material["month"] = month
        completed_tasks = [
            item for item in material["tasks"] if item.get("status") == "已完成"
        ]
        unfinished_count = int(material["unfinished_count"])
        emotion_summary = material["emotion_summary"]
        material["completed_tasks"] = completed_tasks
        material["todo_count"] = len(completed_tasks)
        return {
            "month_summary": self._format_month_summary(
                len(completed_tasks), unfinished_count, emotion_summary
            ),
        }

    def is_configured(self):
        return self.client.configured

    def choose_avatar(self, text):
        if not self.avatar_paths:
            return ""

        content = str(text)
        keyword_groups = (
            ("爆炸|炸|战斗|危险|刺激|游戏", 0),
            ("哈哈|开心|有趣|好玩|当然", 1),
            ("不懂|麻烦|疑问|为什么|怎么", 2),
            ("累|困|睡觉|休息|懒", 3),
            ("生气|可恶|糟|失败|警告", 4),
            ("厉害|聪明|漂亮|夸|谢谢", 5),
        )
        for pattern, index in keyword_groups:
            if re.search(pattern, content) and index < len(self.avatar_paths):
                return str(self.avatar_paths[index])

        avatar = self.avatar_paths[self._avatar_cursor % len(self.avatar_paths)]
        self._avatar_cursor += 1
        return str(avatar)

    def ask(self, question, record_user=True):
        self.rollover_history()
        question = str(question or "").strip()
        if not question:
            raise DeepSeekError("问题不能为空")

        if record_user:
            self.store.add("user", question)
        context = self.store.context_messages(limit=20)
        memory = self._read_memory()
        system_content = W_PERSONA_PROMPT
        if memory:
            system_content += f"\n\n此前的长期记忆摘要：\n{memory}"
        system_content += f"\n\n当前工作台实况：\n{self._workbench_context()}"
        answer = self.client.chat(
            [{"role": "system", "content": system_content}, *context]
        )
        avatar = self.choose_avatar(answer)
        self.store.add("assistant", answer, avatar=avatar)
        return answer, avatar
