# NO.1 Vivimei Pet Personal Work Table

一个面向 macOS 的桌面宠物与个人工作台，使用 Python、PyQt6 和 Qt WebEngine 构建。

<img src="./assets/图片/png/avatar_1.png" width="180" alt="维维美桌面宠物">

## 功能

- 桌面宠物动作、拖动、落地与自动活动。
- 7 种心情记录与每日情绪统计。
- 待办清单、想法速记、聊天室与复盘工作台。
- 可配置 DeepSeek 兼容接口的聊天与复盘总结。
- 提醒、免打扰和个人工作台数据均保存在本地。

## 环境要求

- macOS 13 或更高版本
- Python 3.12
- 依赖见 `requirements.txt`

## 安装与启动

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -B -u main.py
```

macOS 也可以先双击 `安装依赖.app`，再双击 `启动桌宠.app`。

## API 配置

API 配置位于实际使用的 `vivimei/oc.py`。使用前请填入自己的 API Key、接口地址和模型名：

```python
DEEPSEEK_API_KEY = "你的 API Key"
DEEPSEEK_BASE_URL = "你的 API 接口地址"
DEEPSEEK_MODEL = "你的模型名称"
```

聊天内容会在调用 API 时发送到你配置的服务。没有配置 API 时，不应当把任何密钥提交到 Git 仓库。

## 数据与隐私

首次运行后，应用会在本地创建 `data/`，其中可能包含：

- `data/chat_history/`：聊天记录
- `data/chat_memory/`：聊天摘要
- `data/emotion_history.json`：情绪记录
- `data/workbench_data/`：待办、速记和工作台本地存储
- `data/settings.json`：桌宠设置

`data/` 已被 `.gitignore` 排除。提交或发布前请确认 API 配置和个人数据都没有进入 Git 历史。

## 项目结构

```text
main.py                 应用入口
vivimei/                桌面宠物、聊天、工作台与情绪模块
workbench/index.html    工作台前端
assets/                 动作、图片与应用图标
tests/                  打包与隐私清理测试
tools/                  清洁发布构建脚本
requirements.txt        Python 依赖
```

## 测试与构建

```bash
python -m unittest discover -s tests -v
python tools/build_clean_release.py
```

构建脚本会生成默认的清洁数据模板，并将 API Key、接口地址和模型名替换为占位符。

## 授权与致谢

非常感谢 [OTAXIO/digit_maid](https://github.com/OTAXIO/digit_maid) 项目提供的素材参考（老大做的真好真可爱太喜欢了）。原始角色美术和素材版权归各自权利方所有。本仓库仅用于个人、非商业的桌宠+工作台功能的使用，不是 Codex、OpenAI 或游戏官方素材发布。
