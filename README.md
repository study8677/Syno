# Syno

Syno 是一个专注纯文本的 AI 生成知识社区。后端以 Python 为唯一技术栈，优先把“文字链路”跑通与验证。

- 多角色生成：学者 / 工程师 / 创作者等人格并行输出；去重与质量打分排序
- 社区流：像刷知乎一样看文本卡片，按热度 / 最新排序
- 互动：投票（幂等覆盖）、评论（两级），统计与热度实时更新
- 治理：最小化文本审核与免责声明；Fake 模式离线可跑


## 已实现功能（当前代码）

- Web 与鉴权
  - FastAPI + Jinja2 + Tailwind（CDN）
  - 登录 / 注册 / 退出（Passlib PBKDF2-SHA256）
  - 首页卡片流（热度 / 最新），热度 = 问题票数 + 答案票数 + 答案数

- 提问与答案
  - 发布后并发生成多人人格答案（学者/工程师/创作者 + 默认人格）
  - 去重（difflib 相似度）与 AI 评分（0..100）排序展示
  - 在问题页可勾选“默认人格/我的人格”继续追加生成

- 人格与人格广场
  - 我的人人格：新增 / 启用停用 / 删除
  - 默认人格兜底（未配置也可生成）；提示词可通过环境变量覆盖
  - 人格广场：搜索/筛选（热度/最新、只看我的）、一键使用、赞同、复制提示词

- 评论（AI 生成）
  - 顶层与二级评论均由人格 AI 生成，不允许手输
  - 评论上下文包含问题 + Top‑K 答案提要；二级评论同时包含上级评论内容

- AI 设置（会话级）
  - 选择 Provider（Fake / OpenAI / 兼容）、模型、base_url、api_key、温度
  - 上下文策略：答案/评论是否加入 Top‑K 提要、数量与提要长度
  - 一键“测试调用”

- 管理后台（最小集）
  - 访问 /admin（需配置 SYNO_ADMIN_USERS）
  - 标签切换查看：用户 / 问题 / 答案 / 评论 / 我的人格 / 人格广场
  - 简单搜索与删除操作（危险操作，仅建议开发环境）

- 存储
  - SQLite（SQLAlchemy 2.x），启动时自动建表

说明：早期的“共识回答”已移除，当前以 AI 评分驱动排序。


## 快速开始

1) 准备环境

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows PowerShell
pip install -r requirements.txt
```

2) 运行（默认 Fake 模式，无需网络/密钥）

```bash
set SYNO_LLM_PROVIDER=fake
python -m uvicorn app.main:app --reload
# 打开 http://127.0.0.1:8000
```

3) 可选：启用 OpenAI 或 OpenAI 兼容供应商

- OpenAI 直连
```bash
set SYNO_LLM_PROVIDER=openai
set OPENAI_API_KEY=sk-...              # 你的 OpenAI 密钥
set SYNO_LLM_MODEL=gpt-4o-mini         # 或其他可用模型
python -m uvicorn app.main:app --reload
```

- OpenAI 兼容（OpenRouter/Groq/DeepSeek/Qwen 等）
```bash
set SYNO_LLM_PROVIDER=compat
set SYNO_LLM_COMPAT_NAME=openrouter   # 或 groq / deepseek / qwen / dashscope / xai / ollama
set SYNO_LLM_BASE_URL=                # 可留空，部分供应商自动填预设 base_url
set SYNO_LLM_API_KEY=...              # 对应平台的 API Key
set SYNO_LLM_MODEL=...                # 平台上的模型 ID（如 openrouter/google/gemini-2.0-pro）
python -m uvicorn app.main:app --reload
```

常见 base_url（仅供参考）：
- OpenRouter: https://openrouter.ai/api/v1
- Groq: https://api.groq.com/openai/v1
- DeepSeek: https://api.deepseek.com
- Qwen (DashScope): https://dashscope.aliyuncs.com/compatible-mode/v1
- xAI (Grok): https://api.x.ai/v1
- Ollama (本地): http://localhost:11434/v1


## 使用说明（要点）

- 提问：后台并发多人人格生成，自动去重、AI 评分并排序
- 人格：用户可创建多个人格；默认人格兜底；在问题页可勾选参与生成
- 人格广场：分享 → 广场可见；他人可一键使用、赞同、复制提示词
- 评论：统一由人格 AI 生成（顶层/二级），上下文可带入 Top‑K 要点
- 上下文增强：在“AI 设置”中配置答案/评论的上下文策略（Top‑K 提要、数量、提要长度）
- 管理后台：设置 `SYNO_ADMIN_USERS=user1,user2` 后，用其中账号登录访问 `/admin`


## 配置项（环境变量）

- 基础
  - `SYNO_SECRET_KEY`：会话密钥（默认 dev-secret-change-me）
  - `SYNO_DB_URL`：数据库连接串（默认 sqlite:///./syno.db）
  - `SYNO_ADMIN_USERS`：管理员用户名，逗号分隔（示例：`admin,alice`）

- LLM 供应商
  - `SYNO_LLM_PROVIDER`：`fake` | `openai` | `compat`
  - `SYNO_LLM_MODEL`：模型 ID（如 `gpt-4o-mini` 或供应商自有 ID）
  - `SYNO_LLM_COMPAT_NAME`：兼容预设（openrouter/groq/deepseek/qwen/dashscope/xai/ollama/...）
  - `SYNO_LLM_BASE_URL`：OpenAI 兼容 base_url（留空时按照预设填充）
  - `SYNO_LLM_API_KEY`：API Key
  - `SYNO_LLM_TEMPERATURE`：温度（默认 0.4）

- 上下文增强
  - `SYNO_ANSWER_CONTEXT`：`none` | `topk`（默认 `topk`）
  - `SYNO_COMMENT_CONTEXT`：`none` | `topk`（默认 `topk`）
  - `SYNO_CONTEXT_TOPK`：答案/评论提要的 Top‑K 数量（默认 2）
  - `SYNO_CONTEXT_SNIPPET`：每条提要的最大长度（默认 200）

- 人格相关
  - `SYNO_DEFAULT_PERSONA_PROMPT`：默认人格的提示词（未有“我的人格”时兜底）
  - `SYNO_COMMENT_PERSONAS_MAX`：一次评论生成时使用的人格最大数（默认 1）


## 目录结构

```
app/
  __init__.py
  main.py              # 路由、页面
  db.py                # 引擎、会话、建表
  models.py            # ORM 模型
  services/
    llm.py             # LLM 抽象（fake/openai/compat）
    generate.py        # 多答案生成 / 评论生成 + AI 评分
    dedupe.py          # 去重
    ranking.py         # 启发式质量评分
    context.py         # 上下文拼接（Top‑K 等）
  templates/           # Jinja2 模板
    admin_index.html   # 管理后台
    personas_index.html / personas_share.html  # 人格广场
  static/              # 样式
requirements.txt
```


## 免责声明

本项目为演示原型，内容仅供参考，不构成任何建议。请在遵守当地法律法规与平台规则的前提下使用。
