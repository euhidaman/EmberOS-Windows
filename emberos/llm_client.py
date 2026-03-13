"""HTTP client for the local BitNet inference server (OpenAI-compatible API)."""

import json
import logging
import time
from typing import Generator, Optional

import requests

logger = logging.getLogger("emberos.llm_client")


class LLMClient:
    """Wrapper around the local BitNet server's OpenAI-compatible chat API."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765,
                 temperature: float = 0.7, max_tokens: int = 512,
                 timeout: int = 300):
        self.base_url = f"http://{host}:{port}"
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._session = requests.Session()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def chat(self, messages: list, temperature: Optional[float] = None,
             max_tokens: Optional[int] = None) -> str:
        """Send a chat completion request and return the assistant message."""
        payload = {
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "stream": False,
        }

        last_error = None
        for attempt in range(3):
            try:
                resp = self._session.post(
                    self._url("/v1/chat/completions"),
                    json=payload,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0]["message"]["content"]
                return ""
            except requests.ConnectionError as e:
                last_error = e
                logger.warning("LLM connection error (attempt %d/3): %s", attempt + 1, e)
                time.sleep(2)
            except requests.HTTPError as e:
                logger.error("LLM HTTP error: %s", e)
                raise
            except Exception as e:
                logger.error("LLM unexpected error: %s", e)
                raise

        raise ConnectionError(f"Failed to connect to LLM server after 3 attempts: {last_error}")

    def stream_chat(self, messages: list, temperature: Optional[float] = None,
                    max_tokens: Optional[int] = None) -> Generator[str, None, None]:
        """Stream chat completion tokens via SSE."""
        payload = {
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "stream": True,
        }

        try:
            resp = self._session.post(
                self._url("/v1/chat/completions"),
                json=payload,
                timeout=self.timeout,
                stream=True,
            )
            resp.raise_for_status()

            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue
        except requests.ConnectionError:
            logger.error("Stream connection failed")
            raise
        except Exception as e:
            logger.error("Stream error: %s", e)
            raise

    def health_check(self) -> bool:
        """Check if the server is healthy."""
        try:
            resp = self._session.get(self._url("/health"), timeout=5)
            return resp.status_code == 200
        except Exception:
            return False
