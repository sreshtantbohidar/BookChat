#!/usr/bin/env python3
"""RAG engine — retrieval + generation with Ollama."""

import json
import urllib.request


class RAGEngine:
    """Retrieval-Augmented Generation engine using Ollama."""

    def __init__(self, vector_store, model="qwen3:14b",
                 ollama_url="http://192.168.1.125:11434",
                 top_k=5):
        self.vs = vector_store
        self.model = model
        self.ollama_url = ollama_url
        self.top_k = top_k

    def _ollama_generate(self, prompt, temperature=0.1, num_predict=4096):
        """Call Ollama /api/generate (non-streaming)."""
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "top_p": 0.9,
            }
        }).encode()
        req = urllib.request.Request(
            f"{self.ollama_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read())
        return data.get("response", "").strip()

    def _build_context(self, query, doc_id=None):
        """Retrieve relevant chunks and build context string."""
        hits = self.vs.search(query, doc_id=doc_id, top_k=self.top_k)
        if not hits:
            return "", []

        context_parts = []
        sources = []
        for i, (text, score, meta) in enumerate(hits):
            context_parts.append(f"[Passage {i+1}]\n{text}")
            sources.append({
                "chunk_idx": meta.get("chunk_idx", i),
                "score": round(score, 4),
                "preview": text[:200].replace("\n", " "),
            })

        context = "\n\n".join(context_parts)
        return context, sources

    def chat(self, query, doc_id=None, history=None, mode="answer"):
        """\        Chat with a document.

        Modes:
          answer    — grounded Q&A from retrieved context
          analyze   — deep analysis, patterns, themes
          predict   — derive predictions/possibilities from content
          summarize — summarize the whole document or a section
        """
        context, sources = self._build_context(query, doc_id)

        if not context:
            # No relevant chunks found — ask LLM to say it doesn\'t know
            return {
                "answer": "I couldn\'t find relevant information in the loaded document to answer that question. Try rephrasing or load a different document.",
                "sources": [],
                "mode": mode,
            }

        system_prompts = {
            "answer": """You are a knowledgeable research assistant. Answer the user\'s question using ONLY the provided context from the document. 

Rules:
- Base your answer strictly on the provided passages. Do not invent facts.
- If the context doesn\'t contain enough information, say so clearly.
- Be specific and cite which passage supports your answer when relevant.
- Be concise but thorough.""",

            "analyze": """You are an expert analyst. Analyze the provided document excerpts deeply.

Identify:
- Key themes, patterns, and relationships
- Underlying assumptions and biases
- Strengths and weaknesses of arguments
- Connections between different ideas
- What\'s missing or underexplored

Provide a structured, insightful analysis.""",

            "predict": """You are a strategic foresight analyst. Based on the document content, derive predictions and possibilities.

Consider:
- What trends or trajectories does the content suggest?
- What are the likely future developments based on the patterns described?
- What scenarios (best case, worst case, most likely) emerge from the information?
- What opportunities or risks does the content imply?
- What would happen if the described trends continue?

Be specific and ground your predictions in the document content. Clearly distinguish between what the document states and what you\'re extrapolating.""",

            "summarize": """You are a professional summarizer. Create a comprehensive, well-organized summary of the provided content.

Include:
- Main ideas and key points
- Important details and evidence
- Logical structure and flow
- Any conclusions or implications

The summary should be self-contained — someone reading only it should understand all the main points.""",
        }

        sys_prompt = system_prompts.get(mode, system_prompts["answer"])

        # Build conversation history context
        history_str = ""
        if history:
            history_str = "\n\nPrevious conversation:\n"
            for turn in history[-6:]:  # last 3 exchanges
                role = turn.get("role", "")
                content_h = turn.get("content", "")
                history_str += f"{role}: {content_h}\n"

        prompt = f"""{sys_prompt}

=== DOCUMENT CONTEXT ===
{context}
=== END CONTEXT ===
{history_str}
=== USER QUESTION ===
{query}
=== ANSWER ==="""

        answer = self._ollama_generate(prompt)

        return {
            "answer": answer,
            "sources": sources,
            "mode": mode,
        }
