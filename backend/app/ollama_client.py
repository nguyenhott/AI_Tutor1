import json
import os
from collections.abc import AsyncIterator

import httpx


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        self.model = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
        self.embedding_model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
        self.embedding_batch_size = int(os.getenv("OLLAMA_EMBED_BATCH_SIZE", "16"))
        self.num_predict = int(os.getenv("OLLAMA_NUM_PREDICT", "700"))
        self.num_ctx = int(os.getenv("OLLAMA_NUM_CTX", "2048"))

    async def check_connection(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Cannot connect to Ollama at {self.base_url}") from exc

    def _payload(self, messages: list[dict], stream: bool, temperature: float) -> dict:
        return {
            "model": self.model,
            "stream": stream,
            "keep_alive": "10m",
            "messages": messages,
            "options": {
                "temperature": temperature,
                "num_predict": self.num_predict,
                "num_ctx": self.num_ctx,
            },
        }

    async def chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=self._payload(messages, stream=False, temperature=temperature),
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            raise OllamaError(f"Ollama returned HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Cannot call Ollama at {self.base_url}") from exc

        try:
            return data["message"]["content"]
        except KeyError as exc:
            raise OllamaError("Unexpected Ollama response shape") from exc

    async def _embed_batch(self, client: httpx.AsyncClient, texts: list[str]) -> list[list[float]]:
        response = await client.post(
            f"{self.base_url}/api/embed",
            json={"model": self.embedding_model, "input": texts},
        )
        response.raise_for_status()
        data = response.json()
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list):
            raise OllamaError("Unexpected Ollama /api/embed response shape")
        return embeddings

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        batch_size = max(1, self.embedding_batch_size)
        embeddings: list[list[float]] = []

        try:
            async with httpx.AsyncClient(timeout=180) as client:
                for start in range(0, len(texts), batch_size):
                    batch = texts[start : start + batch_size]
                    embeddings.extend(await self._embed_batch(client, batch))
            return embeddings
        except httpx.HTTPStatusError as exc:
            raise OllamaError(
                f"Ollama embedding model failed. Pull it first: ollama pull {self.embedding_model}. "
                f"HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Cannot call Ollama embeddings at {self.base_url}") from exc

    async def stream_chat(
        self,
        messages: list[dict],
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=self._payload(messages, stream=True, temperature=temperature),
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        event = json.loads(line)
                        if "message" in event:
                            yield event["message"].get("content", "")
                        if event.get("done"):
                            break
        except httpx.HTTPStatusError as exc:
            raise OllamaError(f"Ollama returned HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Cannot stream from Ollama at {self.base_url}") from exc
