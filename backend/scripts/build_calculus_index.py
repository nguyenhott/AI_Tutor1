from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.document_indexer import index_document_file, parse_keywords
from app.ollama_client import OllamaClient

DEFAULT_PDF_PATH = Path(r"D:\Dai hoc\Calculus\Main textbook\Early_TranscEndEnTals_EighTh_EdiTion.pdf")


async def run() -> None:
    parser = argparse.ArgumentParser(description="Build a RAG index from a PDF/TXT/MD document.")
    parser.add_argument("--pdf", "--file", dest="file", type=Path, default=DEFAULT_PDF_PATH)
    parser.add_argument("--title", default="Calculus: Early Transcendentals, 8th Edition")
    parser.add_argument(
        "--keywords",
        default="",
        help="Optional comma-separated page filter. Omit this to index the whole document.",
    )
    parser.add_argument(
        "--no-embedding",
        action="store_true",
        help="Build chunks only. Retrieval will fall back to keyword scoring.",
    )
    args = parser.parse_args()

    if not args.file.exists():
        raise FileNotFoundError(f"Document not found: {args.file}")

    ollama = OllamaClient()
    result = await index_document_file(
        file_path=args.file,
        title=args.title,
        keywords=parse_keywords(args.keywords),
        embedder=None if args.no_embedding else ollama.embed_texts,
        embedding_model=None if args.no_embedding else ollama.embedding_model,
        replace_existing_document=True,
    )

    print("RAG index build complete")
    for key, value in result.items():
        if key != "skippedPages":
            print(f"{key}: {value}")
    if result.get("skippedPages"):
        print(f"skippedPagesPreview: {result['skippedPages']}")
    if result.get("embeddingError"):
        print("Embedding warning:")
        print(result["embeddingError"])
        print(f"Pull the embedding model if needed: ollama pull {ollama.embedding_model}")


if __name__ == "__main__":
    asyncio.run(run())
