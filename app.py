#!/usr/bin/env python3
"""Flask backend for BookChat — chat with your documents via Mediator agent."""

import os
import json
from pathlib import Path
from flask import Flask, request, jsonify, render_template

from doc_loader import load_document
from vector_store import VectorStore, get_doc_id
from rag_engine import RAGEngine
from mediator import MediatorAgent

# ── Config ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
STORE_DIR = os.path.join(BASE_DIR, "stores")
REGISTRY_FILE = os.path.join(BASE_DIR, "doc_registry.json")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://192.168.1.125:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:14b")
STORE_TYPE = os.environ.get("STORE_TYPE", "chromadb")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(STORE_DIR, exist_ok=True)

# ── App ─────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB

# Document registry (persisted to disk)
doc_registry = {}

# Mediator singleton (created lazily)
_mediator = None
_vs = None


def save_registry():
    """Persist doc_registry to disk."""
    try:
        with open(REGISTRY_FILE, "w") as f:
            json.dump(doc_registry, f, indent=2)
    except Exception as e:
        print(f"[!] Failed to save registry: {e}")


def load_registry():
    """Load doc_registry from disk, rebuild from vector store if needed."""
    global doc_registry
    if os.path.exists(REGISTRY_FILE):
        try:
            with open(REGISTRY_FILE, "r") as f:
                doc_registry = json.load(f)
            print(f"[+] Loaded {len(doc_registry)} documents from registry")
            return
        except Exception as e:
            print(f"[!] Failed to load registry: {e}")
    # Rebuild from vector store if registry file missing
    _rebuild_registry_from_store()


def _rebuild_registry_from_store():
    """Rebuild doc_registry from files on disk + vector store."""
    global doc_registry
    doc_registry = {}
    if not os.path.isdir(UPLOAD_DIR):
        return
    vs = get_vs()
    known_ids = set(vs.list_docs())
    for filename in os.listdir(UPLOAD_DIR):
        filepath = os.path.join(UPLOAD_DIR, filename)
        if not os.path.isfile(filepath):
            continue
        doc_id = get_doc_id(filepath)
        if doc_id in known_ids:
            doc_registry[doc_id] = {
                "filename": filename,
                "path": filepath,
                "chunks": 0,
                "chars": os.path.getsize(filepath),
            }
    if doc_registry:
        save_registry()
        print(f"[+] Rebuilt registry with {len(doc_registry)} documents from disk")


def get_vs():
    global _vs
    if _vs is None:
        _vs = VectorStore(STORE_DIR, store_type=STORE_TYPE, ollama_url=OLLAMA_URL)
    return _vs


def get_mediator():
    global _mediator
    if _mediator is None:
        vs = get_vs()
        rag = RAGEngine(vs, model=DEFAULT_MODEL, ollama_url=OLLAMA_URL)
        _mediator = MediatorAgent(rag, model=DEFAULT_MODEL, ollama_url=OLLAMA_URL)
    return _mediator


# ── Routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    filename = file.filename
    save_path = os.path.join(UPLOAD_DIR, filename)
    file.save(save_path)

    try:
        text = load_document(save_path)
    except Exception as e:
        return jsonify({"error": f"Failed to load document: {e}"}), 400

    if not text.strip():
        return jsonify({"error": "Document is empty"}), 400

    doc_id = get_doc_id(save_path)
    vs = get_vs()
    try:
        num_chunks = vs.ingest(doc_id, text)
    except Exception as e:
        return jsonify({"error": f"Ingestion failed: {e}"}), 500

    doc_registry[doc_id] = {
        "filename": filename,
        "path": save_path,
        "chunks": num_chunks,
        "chars": len(text),
    }
    save_registry()

    # Reset mediator memory for this doc (in case it was re-uploaded)
    med = get_mediator()
    med.clear_memory(doc_id)

    return jsonify({
        "doc_id": doc_id,
        "filename": filename,
        "chunks": num_chunks,
        "chars": len(text),
        "message": f"'{filename}' ingested ({num_chunks} chunks)",
    })


@app.route("/api/documents", methods=["GET"])
def list_documents():
    docs = []
    for doc_id, info in doc_registry.items():
        docs.append({
            "doc_id": doc_id,
            "filename": info["filename"],
            "chunks": info["chunks"],
            "chars": info["chars"],
        })
    return jsonify({"documents": docs})


@app.route("/api/documents/<doc_id>", methods=["DELETE"])
def delete_document(doc_id):
    vs = get_vs()
    vs.delete_doc(doc_id)
    doc_registry.pop(doc_id, None)
    save_registry()
    get_mediator().clear_memory(doc_id)
    return jsonify({"message": "Document deleted"})


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    query = data.get("query", "").strip()
    doc_id = data.get("doc_id")          # single doc or "all"
    mode = data.get("mode", "answer")

    if not query:
        return jsonify({"error": "Empty query"}), 400

    mediator = get_mediator()

    # Multi-document chat: query across all documents
    if doc_id == "all":
        if not doc_registry:
            return jsonify({"error": "No documents loaded. Upload documents first."}), 400
        try:
            result = mediator.process_multi(query, list(doc_registry.keys()), mode=mode)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({"error": f"Generation failed: {e}"}), 500
    else:
        if not doc_id or doc_id not in doc_registry:
            return jsonify({"error": "No document loaded. Upload a document first."}), 400
        try:
            result = mediator.process(query, doc_id=doc_id, mode=mode)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({"error": f"Generation failed: {e}"}), 500

    # Convert numpy types to native Python for JSON serialization
    def _sanitize(obj):
        import numpy as np
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_sanitize(v) for v in obj]
        return obj

    result = _sanitize(result)
    if doc_id == "all":
        result["doc_id"] = "all"
        result["filename"] = f"All documents ({len(doc_registry)})"
    else:
        result["doc_id"] = doc_id
        result["filename"] = doc_registry[doc_id]["filename"]
    return jsonify(result)


@app.route("/api/chat/clear", methods=["POST"])
def clear_chat():
    data = request.get_json()
    doc_id = data.get("doc_id")
    if doc_id:
        get_mediator().clear_memory(doc_id)
    return jsonify({"message": "Chat history cleared"})


@app.route("/api/models", methods=["GET"])
def list_models():
    import urllib.request
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        return jsonify({"models": models, "current": DEFAULT_MODEL})
    except Exception as e:
        return jsonify({"models": [DEFAULT_MODEL], "current": DEFAULT_MODEL, "error": str(e)})


# ── Main ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[*] BookChat starting...")
    print(f"[*] Ollama: {OLLAMA_URL}")
    print(f"[*] Model: {DEFAULT_MODEL}")
    print(f"[*] Store: {STORE_TYPE}")
    load_registry()
    print(f"[*] Open http://localhost:5000 in your browser")
    app.run(host="0.0.0.0", port=5000, debug=False)
