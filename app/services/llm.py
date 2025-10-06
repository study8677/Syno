import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class LLMConfig:
    # provider: fake | openai | compat
    provider: str
    model: str
    # compat parameters (for OpenAI-compatible vendors)
    compat_name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    temperature: float = 0.4


def get_default_config() -> LLMConfig:
    provider = os.getenv("SYNO_LLM_PROVIDER", "fake").lower()
    model = os.getenv("SYNO_LLM_MODEL", "gpt-4o-mini")
    compat_name = os.getenv("SYNO_LLM_COMPAT_NAME")
    base_url = os.getenv("SYNO_LLM_BASE_URL")
    api_key = os.getenv("SYNO_LLM_API_KEY")
    try:
        temperature = float(os.getenv("SYNO_LLM_TEMPERATURE", "0.4"))
    except Exception:
        temperature = 0.4

    # Built-in base_url presets for popular OpenAI-compatible vendors
    PRESET_BASE_URLS = {
        # Aggregators
        "openrouter": "https://openrouter.ai/api/v1",
        # Vendors
        "groq": "https://api.groq.com/openai/v1",
        "deepseek": "https://api.deepseek.com",
        "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",  # Qwen
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "xai": "https://api.x.ai/v1",  # Grok (verify your account + model)
        "ollama": "http://localhost:11434/v1",
        "zhipu": "https://open.bigmodel.cn/api/paas/v4",
        "moonshot": "https://api.moonshot.cn/v1",  # Kimi
        "siliconflow": "https://api.siliconflow.cn/v1",
        "doubao": "https://ark.cn-beijing.volces.com/v1",
        # For Gemini, consider using OpenRouter (gemini-* models) or a provider that exposes OpenAI compatibility.
    }
    def preset_base_url(name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        return PRESET_BASE_URLS.get(name.lower())
    if provider not in {"fake", "openai", "compat"}:
        provider = "fake"
    if provider == "compat" and not base_url and compat_name:
        base_url = preset_base_url(compat_name)

    return LLMConfig(
        provider=provider,
        model=model,
        compat_name=compat_name,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
    )


def config_from_dict(d: Optional[dict]) -> Optional[LLMConfig]:
    if not d:
        return None
    try:
        # try fill preset base_url if compat
        base_url = d.get("base_url")
        provider = d.get("provider", "fake")
        compat_name = d.get("compat_name")
        if provider == "compat" and not base_url and compat_name:
            base_url = preset_base_url(compat_name)

        return LLMConfig(
            provider=d.get("provider", "fake"),
            model=d.get("model", "gpt-4o-mini"),
            compat_name=compat_name,
            base_url=base_url,
            api_key=d.get("api_key"),
            temperature=float(d.get("temperature", 0.4)),
        )
    except Exception:
        return None


class LLMClient:
    def __init__(self, cfg: Optional[LLMConfig] = None) -> None:
        self.cfg = cfg or get_default_config()

    async def generate_answer(
        self,
        persona: str,
        title: str,
        content: Optional[str] = None,
        user_preset: Optional[str] = None,
    ) -> str:
        if self.cfg.provider == "fake":
            return self._fake_answer(persona, title, content, user_preset)
        elif self.cfg.provider in {"openai", "compat"}:
            return await self._openai_like_answer(persona, title, content, user_preset)
        else:
            return self._fake_answer(persona, title, content, user_preset)

    async def summarize_consensus(
        self,
        title: str,
        answers: list[str],
    ) -> dict:
        if self.cfg.provider == "fake":
            return self._fake_consensus(title, answers)
        elif self.cfg.provider in {"openai", "compat"}:
            return await self._openai_like_consensus(title, answers)
        else:
            return self._fake_consensus(title, answers)

    # --- Providers ---
    def _fake_answer(
        self, persona: str, title: str, content: Optional[str], user_preset: Optional[str]
    ) -> str:
        preset = f"（自定义：{user_preset}）" if user_preset else ""
        lines = [
            f"[{persona}{preset}] 针对问题《{title}》：",
            "1) 关键点：简要列出要点与路径。",
            "2) 方法：给出可操作的步骤或结论。",
            "3) 风险：标注限制、假设与边界。",
            "—— 以上为纯文本示例输出。",
        ]
        if content:
            lines.insert(1, f"上下文：{content[:160]}…" if len(content) > 160 else f"上下文：{content}")
        return "\n".join(lines)

    def _fake_consensus(self, title: str, answers: list[str]) -> dict:
        head = f"关于《{title}》的共识："
        conclusion = head + "大部分答案收敛于一组明确可执行的要点。"
        evidence = "依据来自不同人格答案的交集：术语、步骤、边界条件。"
        divergence = "分歧集中在取舍、优先级与实现路径。"
        summary = "建议先跑通最小闭环，再逐步扩展与优化。"
        return {
            "conclusion": conclusion,
            "evidence": evidence,
            "divergence": divergence,
            "summary": summary,
        }

    async def _openai_like_answer(
        self, persona: str, title: str, content: Optional[str], user_preset: Optional[str]
    ) -> str:
        from openai import AsyncOpenAI  # lazy import

        client = AsyncOpenAI(api_key=self.cfg.api_key, base_url=self.cfg.base_url)
        system = (
            f"你是{persona}。以纯文本、结构化、条理清晰的风格回答问题。"
            "不输出图片或链接，优先给出可执行的步骤。"
        )
        if user_preset:
            system += f" 用户自定义偏好：{user_preset}。"
        user_msg = f"问题：{title}\n" + (f"补充：{content}\n" if content else "")
        resp = await client.chat.completions.create(
            model=self.cfg.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            temperature=self.cfg.temperature,
        )
        return resp.choices[0].message.content or ""

    async def _openai_like_consensus(self, title: str, answers: list[str]) -> dict:
        from openai import AsyncOpenAI  # lazy import

        client = AsyncOpenAI(api_key=self.cfg.api_key, base_url=self.cfg.base_url)
        system = (
            "你是共识引擎，负责从多份答案中总结结论/依据/分歧/小结，"
            "以中文、纯文本、简明结构化输出。"
        )
        joined = "\n\n---\n\n".join(answers)
        user_msg = f"问题：{title}\n\n以下是不同人格的答案：\n{joined}\n\n请输出：结论/依据/分歧/小结。"
        resp = await client.chat.completions.create(
            model=self.cfg.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
            temperature=min(0.9, max(0.0, self.cfg.temperature - 0.2)),
        )
        text = resp.choices[0].message.content or ""
        # naive split for now
        def pick(tag: str) -> str:
            for line in text.splitlines():
                if tag in line:
                    return line.split(tag, 1)[-1].strip(" ：:—-")
            return text

        return {
            "conclusion": pick("结论"),
            "evidence": pick("依据"),
            "divergence": pick("分歧"),
            "summary": pick("小结"),
        }
