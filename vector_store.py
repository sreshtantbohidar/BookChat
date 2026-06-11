#!/usr/bin/env python3
"""Vector store module — supports ChromaDB and FAISS."""

import os
import json
import hashlib


def get_doc_id(filepath):
    """Generate a stable ID from file path + mtime."""
    stat = os.stat(filepath)
    raw = f"{filepath}:{stat.st_size}:{stat.st_mtime}"
    return hashlib.md5(raw.encode()).hexdigest()


class VectorStore:
    """Unified vector store interface supporting ChromaDB and FAISS."""

    def __init__(self, store_dir, store_type="chromadb", embed_model="nomic-embed-text",
                 ollama_url="http://192.168.1.125:11434"):
        self.store_dir = store_dir
        self.store_type = store_type
        self.embed_model = embed_model
        self.ollama_url = ollama_url
        self._embedder = None
        os.makedirs(store_dir, exist_ok=True)

    def _get_embedder(self):
        if self._embedder is None:
            if self.store_type == "chromadb":
                from langchain_ollama import OllamaEmbeddings
                self._embedder = OllamaEmbeddings(
                    model=self.embed_model,
                    base_url=self.ollama_url,
                )
            else:
                from langchain_community.embeddings import HuggingFaceEmbeddings
                self._embedder = HuggingFaceEmbeddings(
                    model_name="sentence-transformers/all-MiniLM-L6-v2"
                )
        return self._embedder

    def _get_ollama_embed(self, texts):
        """Embed texts via Ollama /api/embeddings endpoint."""
        import urllib.request
        embeddings = []
        for text in texts:
            payload = json.dumps({
                "model": self.embed_model,
                "prompt": text[:4096],
            }).encode()
            req = urllib.request.Request(
                f"{self.ollama_url}/api/embeddings",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
            embeddings.append(data["embedding"])
        return embeddings

    def ingest(self, doc_id, text, chunk_size=1000, chunk_overlap=200):
        """Chunk text, embed, and store in vector DB."""
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        chunks = splitter.split_text(text)
        chunks = [c for c in chunks if c.strip()]
        print(f"[+] Split into {len(chunks)} chunks for doc {doc_id[:8]}...")

        if self.store_type == "chromadb":
            return self._ingest_chromadb(doc_id, chunks)
        else:
            return self._ingest_faiss(doc_id, chunks)

    def _ingest_chromadb(self, doc_id, chunks):
        import chromadb
        from chromadb.config import Settings

        client = chromadb.PersistentClient(
            path=os.path.join(self.store_dir, "chroma"),
            settings=Settings(anonymized_telemetry=False),
        )
        collection = client.get_or_create_collection(
            name="bookchat",
            metadata={"hnsw:space": "cosine"},
        )

        # Delete existing chunks for this doc
        existing = collection.get(where={"doc_id": doc_id})
        if existing["ids"]:
            collection.delete(ids=existing["ids"])

        # Embed via Ollama
        embeddings = self._get_ollama_embed(chunks)

        ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
        metadatas = [{"doc_id": doc_id, "chunk_idx": i} for i in range(len(chunks))]

        collection.add(
            ids=ids,
            documents=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        print(f"[+] Stored {len(chunks)} chunks in ChromaDB")
        return len(chunks)

    def _ingest_faiss(self, doc_id, chunks):
        import faiss
        import numpy as np
        from langchain_community.vectorstores import FAISS
        from langchain_ollama import OllamaEmbeddings

        faiss_dir = os.path.join(self.store_dir, "faiss")
        os.makedirs(faiss_dir, exist_ok=True)
        index_path = os.path.join(faiss_dir, f"{doc_id}")

        # Use Ollama embeddings (same as ChromaDB path) to avoid HF model download
        embedder = OllamaEmbeddings(
            model=self.embed_model,
            base_url=self.ollama_url,
        )

        vs = FAISS.from_texts(chunks, embedder, metadatas=[
            {"doc_id": doc_id, "chunk_idx": i} for i in range(len(chunks))
        ])
        vs.save_local(index_path)
        print(f"[+] Stored {len(chunks)} chunks in FAISS ({index_path})")
        return len(chunks)

    def search(self, query, doc_id=None, top_k=5):
        """Search for relevant chunks. Returns list of (text, score, metadata)."""
        if self.store_type == "chromadb":
            return self._search_chromadb(query, doc_id, top_k)
        else:
            return self._search_faiss(query, doc_id, top_k)

    def _search_chromadb(self, query, doc_id, top_k):
        import chromadb
        from chromadb.config import Settings

        client = chromadb.PersistentClient(
            path=os.path.join(self.store_dir, "chroma"),
            settings=Settings(anonymized_telemetry=False),
        )
        try:
            collection = client.get_collection("bookchat")
        except Exception:
            return []

        # Embed query via Ollama
        q_emb = self._get_ollama_embed([query])[0]

        where_filter = {"doc_id": doc_id} if doc_id else None
        results = collection.query(
            query_embeddings=[q_emb],
            n_results=top_k,
            where=where_filter,
        )

        hits = []
        if results["ids"] and results["ids"][0]:
            for i, doc in enumerate(results["documents"][0]):
                dist = results["distances"][0][i] if results.get("distances") else 0
                hits.append((doc, dist, results["metadatas"][0][i]))
        return hits

    def _search_faiss(self, query, doc_id, top_k):
        import faiss
        from langchain_community.vectorstores import FAISS
        from langchain_ollama import OllamaEmbeddings

        faiss_dir = os.path.join(self.store_dir, "faiss")
        if not os.path.isdir(faiss_dir):
            return []

        embedder = OllamaEmbeddings(
            model=self.embed_model,
            base_url=self.ollama_url,
        )

        all_hits = []
        for entry in os.listdir(faiss_dir):
            if doc_id and entry != doc_id:
                continue
            idx_path = os.path.join(faiss_dir, entry)
            if not os.path.isfile(os.path.join(idx_path, "index.faiss")):
                continue
            try:
                vs = FAISS.load_local(idx_path, embedder, allow_dangerous_deserialization=True)
                results = vs.similarity_search_with_relevance_scores(query, k=top_k)
                for doc, score in results:
                    all_hits.append((doc.page_content, 1.0 - score, doc.metadata))
            except Exception as e:
                print(f"[!] FAISS load error for {entry}: {e}")

        all_hits.sort(key=lambda x: x[1])
        return all_hits[:top_k]

    def list_docs(self):
        """List all ingested document IDs."""
        if self.store_type == "chromadb":
            return self._list_chromadb()
        else:
            return self._list_faiss()

    def _list_chromadb(self):
        import chromadb
        from chromadb.config import Settings
        try:
            client = chromadb.PersistentClient(
                path=os.path.join(self.store_dir, "chroma"),
                settings=Settings(anonymized_telemetry=False),
            )
            col = client.get_collection("bookchat")
            metas = col.get()["metadatas"]
            return list(set(m["doc_id"] for m in metas if m))
        except Exception:
            return []

    def _list_faiss(self):
        faiss_dir = os.path.join(self.store_dir, "faiss")
        if not os.path.isdir(faiss_dir):
            return []
        return [d for d in os.listdir(faiss_dir)
                if os.path.isfile(os.path.join(faiss_dir, d, "index.faiss"))]

    def delete_doc(self, doc_id):
        """Remove a document from the store."""
        if self.store_type == "chromadb":
            import chromadb
            from chromadb.config import Settings
            client = chromadb.PersistentClient(
                path=os.path.join(self.store_dir, "chroma"),
                settings=Settings(anonymized_telemetry=False),
            )
            col = client.get_collection("bookchat")
            existing = col.get(where={"doc_id": doc_id})
            if existing["ids"]:
                col.delete(ids=existing["ids"])
        else:
            import shutil
            idx_path = os.path.join(self.store_dir, "faiss", doc_id)
            if os.path.isdir(idx_path):
                shutil.rmtree(idx_path)
        print(f"[+] Deleted doc {doc_id[:8]}... from store")
