# BookChat — Chat with Your Documents

A RAG-based chat application that lets you upload documents (PDF, Word, Excel, CSV, JSON, HTML, EPUB, images, etc.) and chat with them using a local Ollama LLM. Features an AI mediator that rewrites queries, decomposes complex questions, and proactively surfaces insights.

## Features

- **21 document formats** — PDF (text + OCR), DOCX, XLSX, XLS, CSV, JSON, HTML, XML, EPUB, RTF, PNG, JPG, TIFF, BMP, WebP, TXT, MD
- **4 chat modes** — Answer, Analyze, Predict, Summarize
- **Multi-document chat** — query across all uploaded documents or select a specific one
- **Persistent registry** — uploaded documents survive server restarts
- **AI Mediator** — query rewriting, multi-step reasoning, follow-up suggestions, proactive insights
- **Source citations** — every answer cites which document passages support it
- **Dark-themed UI** — responsive chat interface with drag-and-drop upload

## Architecture

```
bookchat/
├── app.py              # Flask backend (API endpoints)
├── doc_loader.py       # Universal document loader (21 formats)
├── vector_store.py     # Vector store (ChromaDB + FAISS backends)
├── rag_engine.py       # RAG engine (retrieve + generate)
├── mediator.py         # AI mediator agent (query rewrite, reasoning, followups, insights)
├── requirements.txt    # Python dependencies
├── templates/
│   └── index.html      # Chat UI (dark theme)
├── samples/            # Sample test documents
├── uploads/            # Uploaded documents (gitignored)
└── stores/             # Vector databases (gitignored)
```

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Start Ollama (on your remote machine)
ollama serve
ollama pull qwen3:14b
ollama pull nomic-embed-text

# Run the app
python app.py
# Open http://localhost:5000
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_URL` | `http://192.168.1.125:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `qwen3:14b` | LLM model for chat |
| `STORE_TYPE` | `chromadb` | Vector store: `chromadb` or `faiss` |

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Chat UI |
| POST | `/api/upload` | Upload a document |
| GET | `/api/documents` | List all documents |
| DELETE | `/api/documents/<id>` | Delete a document |
| POST | `/api/chat` | Chat (body: `{query, doc_id, mode}`) |
| POST | `/api/chat/clear` | Clear chat history |
| GET | `/api/models` | List available Ollama models |

## Chat Modes

- **Answer** — Grounded Q&A with source citations
- **Analyze** — Deep analysis of themes, patterns, relationships
- **Predict** — Strategic predictions and possibilities
- **Summarize** — Comprehensive summary of document content

## Multi-Document Chat

- Click a document name to chat with that specific file
- Click "All Docs" to query across all uploaded documents
- Source citations show which document each passage came from
