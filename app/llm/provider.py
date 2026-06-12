import abc
from typing import List, Dict, Any, Optional
from openai import AsyncOpenAI
import httpx

from app.core.config import settings
from app.core.logger import logger

class LLMProvider(abc.ABC):
    @abc.abstractmethod
    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> str:
        """Generate a text completion for the given conversation history.

        Args:
            messages: A list of dicts with 'role' and 'content' keys.
            **kwargs: Extra parameters like 'temperature', 'model', or 'max_tokens'.
        """
        pass

class GroqProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        self.client = AsyncOpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=api_key
        )
        self.model = model

    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> str:
        try:
            model = kwargs.get("model", self.model)
            temperature = kwargs.get("temperature", 0.7)
            max_tokens = kwargs.get("max_tokens", 1024)

            logger.debug(f"Calling Groq LLM API with model: {model}")
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"Groq API call failed: {e}")
            raise

class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> str:
        try:
            model = kwargs.get("model", self.model)
            temperature = kwargs.get("temperature", 0.7)
            max_tokens = kwargs.get("max_tokens", 1024)

            logger.debug(f"Calling OpenAI API with model: {model}")
            response = await self.client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"OpenAI API call failed: {e}")
            raise

class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self.api_key = api_key
        self.model = model

    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> str:
        contents = []
        system_instruction = None
        
        # Convert standard chat messages to Google Gemini format
        for m in messages:
            role = m["role"]
            content = m["content"]
            if role == "system":
                system_instruction = {"parts": [{"text": content}]}
            else:
                gemini_role = "model" if role == "assistant" else "user"
                contents.append({
                    "role": gemini_role,
                    "parts": [{"text": content}]
                })
        
        model = kwargs.get("model", self.model)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        
        payload: Dict[str, Any] = {
            "contents": contents
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
            
        generation_config = {}
        if "temperature" in kwargs:
            generation_config["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            generation_config["maxOutputTokens"] = kwargs["max_tokens"]
        if generation_config:
            payload["generationConfig"] = generation_config

        try:
            logger.debug(f"Calling Gemini API with model: {model}")
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=30.0)
                response.raise_for_status()
                data = response.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            logger.error(f"Gemini API call failed: {e}")
            raise

def get_llm_provider(provider_name: Optional[str] = None) -> LLMProvider:
    """Factory to retrieve a configured LLM provider instance."""
    provider = provider_name or settings.DEFAULT_LLM_PROVIDER
    provider = provider.lower()
    
    if provider == "groq":
        return GroqProvider(api_key=settings.GROQ_API_KEY, model=settings.DEFAULT_LLM_MODEL)
    elif provider == "openai":
        return OpenAIProvider(api_key=settings.OPENAI_API_KEY)
    elif provider == "gemini":
        return GeminiProvider(api_key=settings.GEMINI_API_KEY)
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")
