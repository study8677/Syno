# Syno

Syno 是一个专注纯文本的 AI 生成知识社区。以 Python 为唯一后端技术栈，先跑通“文字链路”：

- 多角色生成：学者 / 工程师 / 创作者等人格并行输出；去重与质量排序
- 共识引擎：从多答案中总结结构化“共识回答”（结论 / 依据 / 分歧 / 小结）
- 社区流：像刷知乎一样看文本卡片流，按热度 / 最新排序
- 互动：投票（幂等覆盖）、评论（一级 / 二级），统计字段与热度分实时更新
- 治理：最小化文本审核与免责声明，离线 Fake 模式本地可跑


## 当前状态（MVP）

- FastAPI + Jinja2 纯文本页面骨架（首页 / 提问 / 问题页 / 登录注册 / 我的人人格）
- SQLite 存储（SQLAlchemy 2.x）
- 多人格生成：学者 / 工程师 / 创作者 +（可选）用户自定义人格
- 去重（difflib 相似度）与质量打分（轻量启发式 0..100）
- 共识引擎：Fake（离线）/ OpenAI（可选）两种模式

待补充：投票、评论、热度排序、全文检索（FTS5）、治理台等。


## 快速开始

1. 准备环境

   ```bash
   python -m venv .venv
   .venv\\Scripts\\activate  # Windows PowerShell
   pip install -r requirements.txt
   ```

2. 运行（默认 Fake 模式，无需网络/密钥）

   ```bash
   set SYNO_LLM_PROVIDER=fake
   python -m uvicorn app.main:app --reload
   # 打开 http://127.0.0.1:8000
   ```

3. 可选：启用 OpenAI 或 OpenAI 兼容供应商

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
     set SYNO_LLM_BASE_URL=                # 可留空，部分供应商会自动填预设 base_url
     set SYNO_LLM_API_KEY=...              # 对应平台的 API Key
     set SYNO_LLM_MODEL=...                # 该平台上的模型 ID（如 openrouter/google/gemini-2.0-pro）
     python -m uvicorn app.main:app --reload
     ```

   - 常见 base_url（仅供参考，以各平台文档为准）
     - OpenRouter: `https://openrouter.ai/api/v1`
     - Groq: `https://api.groq.com/openai/v1`
     - DeepSeek: `https://api.deepseek.com`
     - Qwen (DashScope): `https://dashscope.aliyuncs.com/compatible-mode/v1`
     - xAI (Grok): `https://api.x.ai/v1`
     - Ollama (本地): `http://localhost:11434/v1`


## 功能说明（MVP）

- 提问：发布后在后台并发生成多个人格答案，自动去重、打分并排序，随后生成“共识回答”。
- 我的人人格：每位用户可保存预设提示词，作为一个人格参与生成。
- 离线模式：`SYNO_LLM_PROVIDER=fake` 时本地生成演示文本，不依赖网络。


## 路线图（Next）

- 互动：
  - 幂等覆盖投票（问题 / 答案）
  - 一级 / 二级评论（问题 / 答案）
- 排序：
  - 热度分（时间衰减 + 得分）
  - 首页热度 / 最新切换
- 检索：
  - SQLite FTS5 + 简易 RAG（后续）
- 治理与合规：
  - 轻量文本审核、免责声明
  - 管理面板（最小集合）


## 目录结构

```
app/
  __init__.py
  main.py              # 路由、页面
  db.py                # 引擎、会话、建表
  models.py            # ORM 模型
  services/
    llm.py             # LLM 抽象（fake/openai）
    generate.py        # 提问后生成多答案 + 共识
    dedupe.py          # 去重
    ranking.py         # 质量评分/热度
  templates/           # Jinja2 模板
  static/              # 样式
requirements.txt
```


## 免责声明

本项目为演示性质的知识社区原型，内容仅供参考，不构成任何建议。请在遵守当地法律法规与平台规则的前提下使用。
