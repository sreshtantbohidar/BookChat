#!/usr/bin/env python3
"""
Mediator Agent — Orchestrates intelligent, engaging conversations with documents.

The mediator sits between the user and the RAG engine, providing:
1. Query Rewriting — reformulates vague questions, resolves pronouns/anaphora
2. Multi-Step Reasoning — decomposes complex questions into sub-queries
3. Conversational Memory — tracks entities and context across turns
4. Follow-Up Suggestions — generates 3 engaging follow-up questions
5. Proactive Insights — surfaces interesting patterns every few turns
"""

import json
import urllib.request
import re
from collections import defaultdict


class ConversationMemory:
    """Tracks conversation state: entities, topics, turn history."""

    def __init__(self, max_turns=20):
        self.turns = []
        self.entities = {}
        self.topics = []
        self.max_turns = max_turns

    def add_turn(self, role, content):
        self.turns.append({"role": role, "content": content})
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]

    def get_history_str(self, last_n=6):
        recent = self.turns[-last_n:] if len(self.turns) > last_n else self.turns
        lines = []
        for t in recent:
            role_label = "User" if t["role"] == "user" else "Assistant"
            lines.append(role_label + ": " + t["content"][:300])
        return "\n".join(lines)

    def resolve_anaphora(self, query):
        """Replace pronouns/anaphora with actual entities from context."""
        pronouns = ["it", "they", "them", "their", "its", "this", "that",
                     "these", "those"]
        words = query.lower().split()
        needs_resolution = any(p in words for p in pronouns)

        if not needs_resolution or len(self.turns) < 2:
            return query

        # Extract candidate entities from last few turns
        candidates = []
        entity_pat = re.compile(r'"([^"]+)"|\*\*([^*]+)\*\*|\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b')
        for turn in reversed(self.turns[-4:]):
            if turn["role"] == "assistant":
                found = entity_pat.findall(turn["content"])
                for group in found:
                    for item in group:
                        if item and len(item) > 2:
                            candidates.append(item)

        resolved = query
        for pronoun in pronouns:
            if pronoun in words and candidates:
                resolved = re.sub(
                    r'\b' + pronoun + r'\b',
                    candidates[0],
                    resolved,
                    count=1,
                    flags=re.IGNORECASE
                )
        return resolved


class MediatorAgent:
    """Orchestrates intelligent document conversations."""

    def __init__(self, rag_engine, model="qwen3:14b",
                 ollama_url="http://192.168.1.125:11434"):
        self.rag = rag_engine
        self.model = model
        self.ollama_url = ollama_url
        self.memories = {}
        self.turn_counts = defaultdict(int)
        self._last_insight_turn = {}

    def _get_memory(self, doc_id):
        if doc_id not in self.memories:
            self.memories[doc_id] = ConversationMemory()
        return self.memories[doc_id]

    def _ollama_generate(self, prompt, temperature=0.2, num_predict=2048):
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
            self.ollama_url + "/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
        return data.get("response", "").strip()

    def _is_complex_query(self, query):
        complex_signals = [
            "compare", "contrast", "difference", "versus", "vs",
            "how does", "why does", "what causes", "explain how",
            "relationship between", "connection between",
            "pros and cons", "advantages", "disadvantages",
            "step by step", "walk me through",
            "what are the implications", "what would happen if",
            "analyze", "evaluate", "assess",
        ]
        q_lower = query.lower()
        return any(sig in q_lower for sig in complex_signals) or len(query.split()) > 20

    def _rewrite_query(self, query, memory, mode):
        resolved = memory.resolve_anaphora(query)
        if len(resolved.split()) > 5 and not resolved.startswith("What is"):
            return resolved

        history_str = memory.get_history_str(last_n=4)
        prompt = (
            "You are a query rewriting assistant. Rewrite the user's question "
            "to be more specific and document-searchable.\n\n"
            "Conversation history:\n" + history_str + "\n\n"
            "Original question: " + resolved + "\n"
            "Mode: " + mode + "\n\n"
            "Rules:\n"
            "- Resolve any pronouns to their likely referents from conversation history\n"
            "- Make the question specific and self-contained\n"
            "- Keep it concise (1-2 sentences max)\n"
            "- Do NOT answer the question, just rewrite it\n"
            "- Output ONLY the rewritten question, nothing else\n\n"
            "Rewritten question:"
        )
        rewritten = self._ollama_generate(prompt, temperature=0.1, num_predict=256)
        return rewritten if rewritten else resolved

    def _decompose_query(self, query):
        prompt = (
            "Decompose this complex question into 2-4 simpler sub-questions "
            "that can each be answered independently from a document.\n\n"
            "Question: " + query + "\n\n"
            "Output format (one sub-question per line, numbered):\n"
            "1. [sub-question 1]\n"
            "2. [sub-question 2]\n"
            "...\n\n"
            "Sub-questions:"
        )
        result = self._ollama_generate(prompt, temperature=0.1, num_predict=512)
        sub_queries = []
        for line in result.strip().split("\n"):
            line = line.strip()
            if line:
                cleaned = re.sub(r'^\d+[.\)]\s*', '', line).strip()
                if cleaned and len(cleaned) > 5:
                    sub_queries.append(cleaned)
        return sub_queries[:4]

    def _generate_followups(self, query, answer, memory):
        history_str = memory.get_history_str(last_n=4)
        prompt = (
            "Based on this conversation, suggest 3 engaging follow-up questions "
            "the user might want to ask.\n\n"
            "Recent conversation:\n" + history_str + "\n\n"
            "Latest question: " + query + "\n"
            "Latest answer: " + answer[:400] + "\n\n"
            "Rules:\n"
            "- Questions should be natural and build on what was just discussed\n"
            "- Make them specific, not generic\n"
            "- Vary the depth: one factual, one analytical, one exploratory\n"
            "- Each question should be 1 sentence\n"
            "- Output ONLY the 3 questions, numbered 1-3, nothing else\n\n"
            "Follow-up questions:"
        )
        result = self._ollama_generate(prompt, temperature=0.3, num_predict=256)
        questions = []
        for line in result.strip().split("\n"):
            line = line.strip()
            if line:
                cleaned = re.sub(r'^\d+[.\)]\s*', '', line).strip()
                if cleaned and len(cleaned) > 5:
                    questions.append(cleaned)
        return questions[:3]

    def _generate_proactive_insight(self, memory, doc_id):
        hits = self.rag.vs.search(
            "key themes important concepts main ideas",
            doc_id=doc_id, top_k=3
        )
        if not hits:
            return None

        context_lines = []
        for i, (text, _, _) in enumerate(hits):
            context_lines.append("[Passage " + str(i+1) + "]\n" + text[:300])
        context = "\n\n".join(context_lines)
        history_str = memory.get_history_str(last_n=6)

        prompt = (
            "You are a curious, insightful research assistant. Based on the document "
            "excerpts below, share ONE interesting observation, pattern, or connection "
            "that the user might find valuable.\n\n"
            "Document excerpts:\n" + context + "\n\n"
            "Conversation so far:\n" + history_str + "\n\n"
            "Rules:\n"
            "- Share something NOT already covered in the conversation\n"
            "- Make it genuinely interesting\n"
            "- Keep it to 2-3 sentences max\n"
            "- Write it as a natural conversational aside\n"
            "- If there is nothing genuinely interesting, output exactly: NONE\n\n"
            "Insight:"
        )
        result = self._ollama_generate(prompt, temperature=0.4, num_predict=256)
        if result and result.strip().upper() != "NONE" and len(result.strip()) > 20:
            return result.strip()
        return None

    def _synthesize_multi_step(self, query, sub_results):
        parts = []
        for sr in sub_results:
            parts.append("Sub-question: " + sr["query"] + "\nAnswer: " + sr["answer"])
        combined = "\n\n".join(parts)

        prompt = (
            "Synthesize these sub-question answers into one comprehensive, "
            "coherent response to the original question.\n\n"
            "Original question: " + query + "\n\n"
            "Sub-question results:\n" + combined + "\n\n"
            "Rules:\n"
            "- Combine all insights into a unified answer\n"
            "- Remove redundancy while preserving all unique points\n"
            "- Maintain a natural, conversational tone\n"
            "- If sub-answers conflict, acknowledge the tension\n"
            "- Be thorough but well-organized\n\n"
            "Synthesized answer:"
        )
        return self._ollama_generate(prompt, temperature=0.1, num_predict=3072)

    def process(self, query, doc_id, mode="answer"):
        """
        Main entry point. Returns dict with:
          answer, sources, followups, insight, mode, rewritten_query, reasoning_steps
        """
        memory = self._get_memory(doc_id)
        self.turn_counts[doc_id] += 1
        current_turn = self.turn_counts[doc_id]

        # Step 1: Query rewriting
        rewritten = self._rewrite_query(query, memory, mode)

        # Step 2: Multi-step reasoning (if complex)
        reasoning_steps = []
        if self._is_complex_query(rewritten) and mode in ("answer", "analyze"):
            sub_queries = self._decompose_query(rewritten)
            if len(sub_queries) > 1:
                sub_results = []
                for sq in sub_queries:
                    result = self.rag.chat(sq, doc_id=doc_id, history=None, mode=mode)
                    sub_results.append({
                        "query": sq,
                        "answer": result["answer"],
                        "sources": result["sources"],
                    })
                    reasoning_steps.append({
                        "sub_query": sq,
                        "answer_preview": result["answer"][:200],
                    })
                answer = self._synthesize_multi_step(rewritten, sub_results)
                all_sources = []
                for sr in sub_results:
                    all_sources.extend(sr["sources"])
                sources = all_sources[:8]
            else:
                result = self.rag.chat(rewritten, doc_id=doc_id,
                                       history=memory.turns, mode=mode)
                answer = result["answer"]
                sources = result["sources"]
        else:
            result = self.rag.chat(rewritten, doc_id=doc_id,
                                   history=memory.turns, mode=mode)
            answer = result["answer"]
            sources = result["sources"]

        # Step 3: Follow-up suggestions
        followups = self._generate_followups(query, answer, memory)

        # Step 4: Proactive insight (every 4 turns)
        insight = None
        if (mode != "summarize" and
            current_turn % 4 == 0 and
            current_turn != self._last_insight_turn.get(doc_id, 0)):
            insight = self._generate_proactive_insight(memory, doc_id)
            if insight:
                self._last_insight_turn[doc_id] = current_turn

        # Update memory
        memory.add_turn("user", query)
        memory.add_turn("assistant", answer)

        return {
            "answer": answer,
            "sources": sources,
            "followups": followups,
            "insight": insight,
            "mode": mode,
            "rewritten_query": rewritten,
            "reasoning_steps": reasoning_steps,
        }

    def clear_memory(self, doc_id):
        if doc_id in self.memories:
            del self.memories[doc_id]
        self.turn_counts.pop(doc_id, None)
        self._last_insight_turn.pop(doc_id, None)

    def process_multi(self, query, doc_ids, mode="answer"):
        """
        Chat across multiple documents.
        Retrieves top chunks from ALL specified docs, then generates a unified answer.
        """
        from rag_engine import RAGEngine

        # Step 1: Query rewriting (use first doc's memory for context)
        primary_doc = doc_ids[0]
        memory = self._get_memory(primary_doc)
        self.turn_counts[primary_doc] += 1
        current_turn = self.turn_counts[primary_doc]

        rewritten = self._rewrite_query(query, memory, mode)

        # Step 2: Retrieve from ALL documents
        all_hits = []
        per_doc_sources = {}
        for did in doc_ids:
            hits = self.rag.vs.search(rewritten, doc_id=did, top_k=5)
            per_doc_sources[did] = hits
            all_hits.extend(hits)

        # Sort by score and take top 8 overall
        all_hits.sort(key=lambda x: x[1])
        top_hits = all_hits[:8]

        if not top_hits:
            return {
                "answer": "I couldn't find relevant information in any of the loaded documents to answer that question. Try rephrasing or load different documents.",
                "sources": [],
                "mode": mode,
                "rewritten_query": rewritten,
                "doc_ids": doc_ids,
            }

        # Build context with document labels
        context_parts = []
        sources = []
        for i, (text, score, meta) in enumerate(top_hits):
            doc_id_label = meta.get("doc_id", "?")
            fname = "Document"
            # Try to find filename from app's doc_registry — use doc_id prefix
            context_parts.append(f"[Passage {i+1} | doc: {doc_id_label[:8]}...]\n{text}")
            sources.append({
                "chunk_idx": meta.get("chunk_idx", i),
                "score": round(score, 4),
                "preview": text[:200].replace("\n", " "),
                "doc_id": doc_id_label,
            })

        context = "\n\n".join(context_parts)

        # Step 3: Generate answer using RAG engine's prompt builder
        system_prompts = {
            "answer": "You are a knowledgeable research assistant. Answer the user's question using ONLY the provided context from the documents.\n\nRules:\n- Base your answer strictly on the provided passages. Do not invent facts.\n- If the context doesn't contain enough information, say so clearly.\n- Be specific and cite which passage supports your answer.\n- When information comes from multiple documents, note which document each point comes from.\n- Be concise but thorough.",
            "analyze": "You are an expert analyst. Analyze the provided document excerpts deeply from multiple sources.\n\nIdentify:\n- Key themes, patterns, and relationships across documents\n- Points of agreement and disagreement between sources\n- Underlying assumptions and biases\n- What's missing or underexplored\n\nProvide a structured, insightful analysis.",
            "predict": "You are a strategic foresight analyst. Based on content from multiple documents, derive predictions and possibilities.\n\nConsider:\n- What trends or trajectories do the documents collectively suggest?\n- What scenarios emerge from the combined information?\n- What opportunities or risks do the documents imply?\n\nBe specific and ground your predictions in the document content.",
            "summarize": "You are a professional summarizer. Create a comprehensive, well-organized summary of the provided content from multiple documents.\n\nInclude:\n- Main ideas and key points from each document\n- Important details and evidence\n- Connections and themes across documents\n\nThe summary should be self-contained.",
        }

        sys_prompt = system_prompts.get(mode, system_prompts["answer"])
        history_str = ""
        if memory.turns:
            history_str = "\n\nPrevious conversation:\n"
            for turn in memory.turns[-6:]:
                role = turn.get("role", "")
                content_h = turn.get("content", "")
                history_str += f"{role}: {content_h}\n"

        prompt = f"""{sys_prompt}

=== DOCUMENT CONTEXT (from {len(doc_ids)} documents) ===
{context}
=== END CONTEXT ===
{history_str}
=== USER QUESTION ===
{query}
=== ANSWER ==="""

        answer = self._ollama_generate(prompt)

        # Step 4: Follow-up suggestions
        followups = self._generate_followups(query, answer, memory)

        # Step 5: Proactive insight (every 4 turns)
        insight = None
        if mode != "summarize" and current_turn % 4 == 0 and current_turn != self._last_insight_turn.get(primary_doc, 0):
            insight = self._generate_proactive_insight(memory, primary_doc)
            if insight:
                self._last_insight_turn[primary_doc] = current_turn

        # Update memory
        memory.add_turn("user", query)
        memory.add_turn("assistant", answer)

        return {
            "answer": answer,
            "sources": sources,
            "followups": followups,
            "insight": insight,
            "mode": mode,
            "rewritten_query": rewritten,
            "doc_ids": doc_ids,
        }
