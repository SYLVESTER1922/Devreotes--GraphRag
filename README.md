# Devreotes Lab GraphRAG Research Assistant

**Sylvester Vhashe & Tadiwanashe C. Dzimbanete**
DAV 6500 Capstone Project | Katz School of Science and Health, Yeshiva University | May 2026

---

## What We Built

We built a chatbot that makes Professor Peter Devreotes' entire body of research at Johns Hopkins School of Medicine searchable through natural language. The lab has published 238 papers over 50 years (1975–2025) covering cell migration, chemotaxis, and signal transduction. Before our tool, there was no way to quickly search across all papers, find connections between proteins and methods, or get cited answers to research questions.

Our solution uses Graph Retrieval-Augmented Generation (GraphRAG) — we extracted structured entities from every paper and stored them in a Neo4j knowledge graph. When a user asks a question, the system searches the graph, retrieves relevant papers and entities, and sends that context to GPT-4o-mini to generate a natural language answer with specific paper citations.

**Live Demo:** [huggingface.co/spaces/Sylvester1922/Devreotes-graphRag](https://huggingface.co/spaces/Sylvester1922/Devreotes-graphRag)

---

## How It Works

The system follows a six-step pipeline:

### 1. PDF Text Extraction
We downloaded 238 PDFs from the professor's Dropbox. Most were digital (214 papers), but 24 older papers from the 1970s–1980s were scanned images. We used PyMuPDF for digital PDFs and GPT-4o-mini Vision for the scanned ones to get text from everything.

### 2. Entity Extraction
Each paper's text goes through GPT-4o-mini with a structured prompt that pulls out proteins, methods, concepts, organisms, key findings, title, year, and authors. The output is structured JSON — not raw text chunks like traditional RAG.

### 3. Knowledge Graph Construction
We loaded all extracted entities into Neo4j Aura as a graph with five node types and five relationship types:

```
(:Paper) -[:MENTIONS_PROTEIN]→ (:Protein)
(:Paper) -[:MENTIONS_GENE]→ (:Gene)
(:Paper) -[:USES_METHOD]→ (:Method)
(:Paper) -[:EXPLORES_CONCEPT]→ (:Concept)
(:Paper) -[:USES_ORGANISM]→ (:Organism)
```

The result is a web of connections — Paper #276 links to Ras, which also links to Papers #272, #278, and #269. You can traverse these connections to find relationships that would take hours to discover by reading papers individually.

### 4. Query Engine
When someone asks a question, the system classifies intent using keyword matching (no GPT needed here), corrects spelling if the question is long enough, extracts keywords, and runs multiple Cypher queries against the graph — searching across proteins, methods, concepts, organisms, paper titles, and findings. No embeddings, no vector database. The graph structure does the filtering.

### 5. Answer Generation
The relevant graph context (typically 5–10 matching papers) plus conversation history (last 8 messages) goes to GPT-4o-mini with a system prompt that instructs it to answer only from the provided data, always cite paper numbers, and never make things up.

### 6. Deployment
The chatbot runs on Hugging Face Spaces using Gradio. The graph lives on Neo4j Aura. All credentials are stored as environment secrets.

---

## Graph Statistics

| Entity | Count |
|--------|-------|
| Papers | 238 |
| Proteins | 738 |
| Genes | 738 |
| Methods | 531 |
| Concepts | 330 |
| Organisms | 228 |
| Relationships | 5,000+ |

---

## Features We Built

**Research Q&A** — Ask anything about the lab's research and get answers with paper citations. Handles follow-ups, comparisons, decade filtering, entity rankings, and cross-paper connections.

**Voice Input** — Speak your question using the microphone. We use OpenAI's Whisper API for transcription, supporting both English and French.

**Bilingual Support** — Toggle between English and French. The entire interface adapts — responses, suggested questions, greetings, and error messages.

**Paper Upload** — Upload a new PDF and the system extracts entities, checks relevance, detects duplicates, and adds it to the graph with password protection. The paper is immediately queryable.

**Analytics Dashboard** — Live stats pulled from Neo4j showing papers by decade, top proteins, top methods, and top concepts.

**Paper Finder** — Enter a paper number to see its full details — title, authors, journal, findings, all connected entities, and a Google Scholar link.

**Follow-up Suggestions** — After every answer, the chatbot suggests three follow-up questions you might want to explore. They're clickable and update dynamically.

**Spelling Correction** — Typos in protein names or scientific terms get corrected before searching the graph.

**Chat Export** — Download the full conversation as a text file.

**Search History** — Recent questions are saved and clickable for instant replay with cached answers (no additional API cost).

---

## Why GraphRAG Instead of Traditional RAG

We chose GraphRAG over vector-based RAG for a few reasons:

- **Structured relationships** — We know PTEN connects to PI3K through specific papers, not just that they appear near similar text
- **Cross-paper queries** — "How is PTEN connected to Ras?" finds papers mentioning both through graph traversal
- **No embedding cost** — Graph queries are free; vector databases need embedding API calls on every rebuild
- **Transparency** — You can trace exactly which papers and entities contributed to an answer

The trade-off is that entity extraction isn't perfect (about 80–85% accuracy on digital PDFs, lower on scanned ones), and some entity names aren't fully normalized. But even with these limitations, the tool gives researchers a search capability they didn't have before.

---

## Cost

| Item | Cost |
|------|------|
| Building the graph (one-time) | ~$3.50 |
| Per query | ~$0.0004 |
| Hosting | $0 |
| Monthly estimate (100 queries) | ~$0.04 |

---

## Tech Stack

- **Neo4j Aura** — Graph database (free tier)
- **GPT-4o-mini** — Entity extraction, spelling correction, answer generation
- **Whisper API** — Voice transcription
- **PyMuPDF** — PDF text extraction
- **Gradio** — Web UI framework
- **Hugging Face Spaces** — Hosting (free tier)
- **Python** — Everything else

---

## Repository Contents

```
├── app.py                    # Chatbot application (deployed on Hugging Face)
├── requirements.txt          # Python dependencies
├── load_graph.cypher         # Cypher statements to rebuild the knowledge graph
├── notebooks/
│   └── GraphRag_Chatbot.ipynb  # Colab notebook for graph loading
├── scripts/
│   └── reload_graph.py       # Script to reload graph from scratch
└── README.md
```

---

## Running Locally

1. Clone this repo
2. Install dependencies: `pip install neo4j openai PyMuPDF gradio`
3. Set environment variables:
   ```
   NEO4J_URI=neo4j+s://your-instance.databases.neo4j.io
   NEO4J_USER=your-username
   NEO4J_PASSWORD=your-password
   OPENAI_API_KEY=your-key
   UPLOAD_PASSWORD=your-upload-password
   ```
4. Run: `python app.py`

---

## Known Limitations

- Scanned PDFs from the 1970s–1980s extract at roughly 50–60% accuracy
- Some entity names exist as separate nodes when they should be merged (e.g., "adenylate cyclase" and "adenylyl cyclase")
- Neo4j Aura free tier auto-deletes after extended inactivity — we keep a backup Cypher file to rebuild quickly
- Search history resets when the app restarts

---

## What's Next

- **Voice responses** using Professor Devreotes' cloned voice (with consent) via ElevenLabs
- **Interactive graph explorer** for visual browsing of entity connections
- **Multi-lab scaling** — the architecture is reusable for any research group

---

*Built as part of the M.S. Data Analytics & Visualization program at the Katz School of Science and Health, Yeshiva University.*
