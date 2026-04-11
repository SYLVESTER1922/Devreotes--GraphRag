import os
import re
import gradio as gr
from openai import OpenAI
from neo4j import GraphDatabase

# ---- Credentials from HF Secrets ----
NEO4J_URI = os.environ.get("NEO4J_URI", "neo4j+s://9f45c691.databases.neo4j.io")
NEO4J_USER = os.environ.get("NEO4J_USER", "9f45c691")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "0Mnrcu_ZtECegn-6kYoRPNHvpYLBiOjNpqkygC4e9gM")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
client = OpenAI()
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# ============================================================
# KEYWORD EXTRACTION
# ============================================================
def extract_keywords(question):
    stop_words = {'what','which','where','when','how','does','about','tell','show',
                  'find','papers','paper','that','this','with','from','have','been',
                  'their','they','there','were','using','used','use','the','and','for',
                  'are','can','you','me','is','it','in','of','to','do','a','an',
                  'on','by','has','was','be','or','at','its','who','did','will',
                  'would','could','should','between','through','role','most',
                  'many','some','any','all','more','than','these','those','into',
                  'also','but','not','only','such','other','each','every','much',
                  'study','studies','research','describe','explain','discuss',
                  'related','involved','connection','connected','relationship',
                  'know','known','like','make','makes','give','list','name'}
    words = re.findall(r'[a-zA-Z0-9/()-]+', question)
    keywords = [w for w in words if w.lower() not in stop_words and len(w) > 2]
    return keywords

# ============================================================
# GRAPH QUERY ENGINE
# ============================================================
def fuzzy_query_graph(question):
    keywords = extract_keywords(question)
    question_lower = question.lower()
    context_parts = []
    seen_papers = set()

    with driver.session() as session:

        for kw in keywords:
            kw_lower = kw.lower()
            search_terms = [kw_lower]
            if len(kw_lower) >= 5:
                search_terms.append(kw_lower[1:])
                search_terms.append(kw_lower[:-1])

            for term in search_terms:
                result = session.run("""
                    MATCH (p:Paper)-[r:MENTIONS_PROTEIN]->(pr:Protein)
                    WHERE toLower(pr.name) CONTAINS $q
                    RETURN p.number AS paper_num, p.title AS title, p.year AS year,
                           pr.name AS protein, r.context AS context
                    ORDER BY p.year DESC LIMIT 10
                """, q=term)
                records = list(result)
                if records:
                    context_parts.append(f"PROTEIN MATCHES for '{kw}':")
                    for r in records:
                        if r['paper_num'] not in seen_papers:
                            context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}' mentions {r['protein']} — {r['context']}")
                            seen_papers.add(r['paper_num'])
                    break

                result = session.run("""
                    MATCH (p:Paper)-[r:USES_METHOD]->(m:Method)
                    WHERE toLower(m.name) CONTAINS $q
                    RETURN p.number AS paper_num, p.title AS title, p.year AS year,
                           m.name AS method, r.context AS context
                    ORDER BY p.year DESC LIMIT 10
                """, q=term)
                records = list(result)
                if records:
                    context_parts.append(f"\nMETHOD MATCHES for '{kw}':")
                    for r in records:
                        if r['paper_num'] not in seen_papers:
                            context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}' uses {r['method']} — {r['context']}")
                            seen_papers.add(r['paper_num'])
                    break

                result = session.run("""
                    MATCH (p:Paper)-[r:EXPLORES_CONCEPT]->(c:Concept)
                    WHERE toLower(c.name) CONTAINS $q
                    RETURN p.number AS paper_num, p.title AS title, p.year AS year,
                           c.name AS concept, r.context AS context
                    ORDER BY p.year DESC LIMIT 10
                """, q=term)
                records = list(result)
                if records:
                    context_parts.append(f"\nCONCEPT MATCHES for '{kw}':")
                    for r in records:
                        if r['paper_num'] not in seen_papers:
                            context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}' explores {r['concept']} — {r['context']}")
                            seen_papers.add(r['paper_num'])
                    break

                result = session.run("""
                    MATCH (p:Paper)-[r:USES_ORGANISM]->(o:Organism)
                    WHERE toLower(o.name) CONTAINS $q
                    RETURN p.number AS paper_num, p.title AS title, p.year AS year,
                           o.name AS organism, r.context AS context
                    ORDER BY p.year DESC LIMIT 10
                """, q=term)
                records = list(result)
                if records:
                    context_parts.append(f"\nORGANISM MATCHES for '{kw}':")
                    for r in records:
                        if r['paper_num'] not in seen_papers:
                            context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}' uses {r['organism']} — {r['context']}")
                            seen_papers.add(r['paper_num'])
                    break

        for kw in keywords:
            result = session.run("""
                MATCH (p:Paper)
                WHERE toLower(p.title) CONTAINS $q OR toLower(p.key_findings) CONTAINS $q
                OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein)
                OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method)
                OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept)
                OPTIONAL MATCH (p)-[:USES_ORGANISM]->(o:Organism)
                RETURN p.number AS paper_num, p.title AS title, p.year AS year,
                       p.key_findings AS findings,
                       collect(DISTINCT pr.name) AS proteins,
                       collect(DISTINCT m.name) AS methods,
                       collect(DISTINCT c.name) AS concepts,
                       collect(DISTINCT o.name) AS organisms
                LIMIT 5
            """, q=kw.lower())
            records = list(result)
            if records:
                new_records = [r for r in records if r['paper_num'] not in seen_papers]
                if new_records:
                    context_parts.append(f"\nPAPER MATCHES for '{kw}':")
                    for r in new_records:
                        context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}'\n    Findings: {r['findings']}\n    Proteins: {r['proteins']}\n    Methods: {r['methods']}")
                        seen_papers.add(r['paper_num'])

        if any(word in question_lower for word in ['how many', 'total', 'overview', 'summary', 'statistics']):
            result = session.run("MATCH (p:Paper) RETURN count(p) AS count")
            pc = result.single()["count"]
            result = session.run("MATCH (pr:Protein) RETURN count(pr) AS count")
            prc = result.single()["count"]
            result = session.run("MATCH (m:Method) RETURN count(m) AS count")
            mc = result.single()["count"]
            result = session.run("MATCH (c:Concept) RETURN count(c) AS count")
            cc = result.single()["count"]
            result = session.run("MATCH (o:Organism) RETURN count(o) AS count")
            oc = result.single()["count"]
            context_parts.append(f"\nGRAPH STATISTICS:\n  Total papers: {pc}\n  Total proteins: {prc}\n  Total methods: {mc}\n  Total concepts: {cc}\n  Total organisms: {oc}")

    if not context_parts:
        return "No matching information found in the knowledge graph for this query."
    return "\n".join(context_parts)

# ============================================================
# CHATBOT LOGIC
# ============================================================
SYSTEM_PROMPT = """You are the Devreotes Lab Research Assistant, a specialized AI for Professor Peter Devreotes' lab at Johns Hopkins University.

The knowledge graph contains 238 papers (1975–2025), 738 proteins, 531 methods, 329 concepts, and 225 organisms.

RULES:
1. Answer in your own words from the provided graph context. Do NOT copy-paste raw context.
2. Keep responses concise — 2-4 sentences for simple questions, more only when necessary.
3. Always cite paper numbers (e.g., "Paper #276").
4. If the context doesn't have enough information, say so honestly.
5. When listing papers, keep descriptions brief — title and one-line relevance.
6. Highlight connections between papers when relevant.
7. If a follow-up references something already discussed, use the conversation history — do NOT introduce unrelated papers.
8. If asked to shorten or summarize a previous answer, rewrite concisely without adding new information.
9. If asked about something not in the graph, say so and suggest related topics.
"""

GREETING_WORDS = {'hi','hello','hey','good morning','good afternoon','good evening',
                  'howdy','greetings','sup','yo',"what's up",'whats up','hola'}

def resolve_followup(question, conversation_history):
    if not conversation_history:
        return question
    question_lower = question.lower().strip()

    summary_triggers = ['summarize', 'summary', 'in short', 'briefly', 'define this',
                        'in a summary', 'short version', 'simplify', 'simpler terms',
                        'in a nutshell', 'quick summary', 'shorter']
    if any(trigger in question_lower for trigger in summary_triggers):
        return "__USE_HISTORY_ONLY__"

    followup_triggers = ['tell me more', 'more about', 'what else', 'which ones',
                         'elaborate', 'expand', 'go on', 'continue', 'and what about',
                         'how about', 'what about', 'why', 'can you explain',
                         'specifically', 'in detail', 'more details']
    is_followup = any(trigger in question_lower for trigger in followup_triggers) or len(question.split()) <= 4

    if is_followup:
        try:
            messages = [
                {"role": "system", "content": "Rewrite the follow-up as a standalone question. Return ONLY the rewritten question."}
            ]
            for turn in conversation_history[-4:]:
                messages.append(turn)
            messages.append({"role": "user", "content": f"Rewrite: {question}"})
            response = client.chat.completions.create(model="gpt-4o-mini", messages=messages, temperature=0, max_tokens=100)
            return response.choices[0].message.content.strip()
        except:
            return question
    return question

def generate_answer(question, conversation_history=None):
    question_stripped = question.strip().lower().rstrip('!?.,:;')

    if question_stripped in GREETING_WORDS or any(question_stripped.startswith(g) for g in GREETING_WORDS):
        return ("Hello! I'm the Devreotes Lab Research Assistant — ask me anything about "
                "Professor Devreotes' 238 published papers on cell migration, chemotaxis, "
                "and signal transduction.")

    off_topic_triggers = ['how are you', 'who are you', 'what are you', 'your name',
                          'what can you do', 'help me']
    if any(trigger in question_stripped for trigger in off_topic_triggers):
        return ("I'm the Devreotes Lab Research Assistant. I can answer questions about "
                "proteins (e.g., PTEN, Ras), methods (e.g., optogenetics), concepts "
                "(e.g., chemotaxis), and more from 238 research papers.")

    if 'thank' in question_stripped:
        return "You're welcome! Feel free to ask more."
    if 'bye' in question_stripped or 'goodbye' in question_stripped:
        return "Goodbye! Come back anytime."

    resolved_question = resolve_followup(question, conversation_history)

    if resolved_question == "__USE_HISTORY_ONLY__":
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if conversation_history:
            for turn in conversation_history[-6:]:
                messages.append(turn)
        messages.append({"role": "user", "content": question})
        response = client.chat.completions.create(model="gpt-4o-mini", messages=messages, temperature=0.3, max_tokens=800)
        return response.choices[0].message.content

    graph_context = fuzzy_query_graph(resolved_question)

    if "No matching information" in graph_context:
        return ("I couldn't find that in the research database. Try asking about specific "
                "proteins (PTEN, Ras, PI3K), methods (optogenetics, confocal microscopy), "
                "or concepts (chemotaxis, cell migration).")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if conversation_history:
        for turn in conversation_history[-6:]:
            messages.append(turn)
    messages.append({"role": "user", "content": f"Based on the following knowledge graph data, answer the question.\n\nGRAPH CONTEXT:\n{graph_context}\n\nQUESTION: {question}"})

    response = client.chat.completions.create(model="gpt-4o-mini", messages=messages, temperature=0.3, max_tokens=800)
    return response.choices[0].message.content

# ============================================================
# GRADIO UI
# ============================================================
custom_css = """
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');

* { font-family: 'DM Sans', sans-serif !important; }

.gradio-container {
    max-width: 100% !important;
    background: #0c1220 !important;
}

#header-row {
    background: linear-gradient(135deg, #0d1b2a 0%, #1b2d4a 50%, #0d1b2a 100%);
    border-bottom: 1px solid rgba(79, 172, 254, 0.15);
    padding: 16px 24px !important;
    margin-bottom: 0 !important;
}

.sidebar-panel {
    background: #111d30 !important;
    border: 1px solid rgba(79, 172, 254, 0.12) !important;
    border-radius: 12px !important;
    padding: 16px !important;
}

.message.user div {
    background: #234b73 !important;
    color: #ffffff !important;
    border-radius: 16px 16px 4px 16px !important;
}

.message.bot div {
    background: #1c2d48 !important;
    color: #f0f6ff !important;
    border-radius: 16px 16px 16px 4px !important;
}

.chatbot .message-wrap .message {
    color: #f0f6ff !important;
}

.suggestion-btn {
    background: #111d30 !important;
    border: 1px solid rgba(79, 172, 254, 0.15) !important;
    color: #8ab4f8 !important;
    border-radius: 8px !important;
    font-size: 13px !important;
    text-align: left !important;
    padding: 10px 14px !important;
}
.suggestion-btn:hover {
    background: #1a2940 !important;
    border-color: rgba(79, 172, 254, 0.4) !important;
}

.stat-card {
    background: linear-gradient(135deg, #111d30, #162440) !important;
    border: 1px solid rgba(79, 172, 254, 0.12) !important;
    border-radius: 10px !important;
    padding: 12px !important;
    text-align: center !important;
}

textarea {
    background: #111d30 !important;
    border: 1px solid rgba(79, 172, 254, 0.2) !important;
    color: #f0f6ff !important;
    border-radius: 12px !important;
}
textarea:focus {
    border-color: rgba(79, 172, 254, 0.5) !important;
}

.section-label {
    color: #4facfe !important;
    font-size: 11px !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 1.5px !important;
    margin-bottom: 8px !important;
}

.dark { background: #0c1220 !important; }
footer { display: none !important; }
"""

search_history = []

def chat_fn(message, history):
    global search_history
    conversation_history = []
    for user_msg, assistant_msg in history:
        conversation_history.append({"role": "user", "content": user_msg})
        conversation_history.append({"role": "assistant", "content": assistant_msg})

    answer = generate_answer(message, conversation_history)

    if message.strip().lower() not in ['hi','hello','hey','thanks','thank you','bye','goodbye']:
        search_history.insert(0, message)
        search_history[:] = search_history[:20]

    return answer

def get_history_display():
    if not search_history:
        return "No searches yet."
    return "\n".join([f"• {q}" for q in search_history[:15]])

with gr.Blocks(css=custom_css, theme=gr.themes.Base(), title="Devreotes Lab — GraphRAG Research Assistant") as demo:

    with gr.Row(elem_id="header-row"):
        gr.HTML("""
            <div style="display:flex; align-items:center; gap:16px;">
                <div style="font-size:36px;">🧬</div>
                <div>
                    <div style="font-size:20px; font-weight:700; color:#e8f0fe; letter-spacing:-0.5px;">
                        Devreotes Lab Research Assistant
                    </div>
                    <div style="font-size:12px; color:#4facfe; font-weight:500; letter-spacing:0.5px;">
                        GraphRAG-powered • 238 Papers • Johns Hopkins University
                    </div>
                </div>
            </div>
        """)

    with gr.Row():
        with gr.Column(scale=1, min_width=220):
            gr.HTML('<div class="section-label">Knowledge Graph</div>')
            with gr.Row():
                gr.HTML('<div class="stat-card"><div style="font-size:22px; font-weight:700; color:#4facfe;">238</div><div style="font-size:10px; color:#5a6d85; text-transform:uppercase; letter-spacing:1px;">Papers</div></div>')
                gr.HTML('<div class="stat-card"><div style="font-size:22px; font-weight:700; color:#4facfe;">738</div><div style="font-size:10px; color:#5a6d85; text-transform:uppercase; letter-spacing:1px;">Proteins</div></div>')
            with gr.Row():
                gr.HTML('<div class="stat-card"><div style="font-size:22px; font-weight:700; color:#4facfe;">531</div><div style="font-size:10px; color:#5a6d85; text-transform:uppercase; letter-spacing:1px;">Methods</div></div>')
                gr.HTML('<div class="stat-card"><div style="font-size:22px; font-weight:700; color:#4facfe;">329</div><div style="font-size:10px; color:#5a6d85; text-transform:uppercase; letter-spacing:1px;">Concepts</div></div>')

            gr.HTML('<div class="section-label" style="margin-top:20px;">Search History</div>')
            history_display = gr.Markdown("No searches yet.", elem_classes=["sidebar-panel"])
            refresh_btn = gr.Button("↻ Refresh", size="sm")
            refresh_btn.click(fn=lambda: get_history_display(), outputs=history_display)

        with gr.Column(scale=3):
            chatbot = gr.Chatbot(height=520, show_label=False, container=False, bubble_full_width=False)
            with gr.Row():
                msg = gr.Textbox(placeholder="Ask about the Devreotes Lab research...", show_label=False, scale=6, container=False)
                send_btn = gr.Button("Send", scale=1, variant="primary")

            gr.HTML('<div class="section-label" style="margin-top:12px;">Suggested Questions</div>')
            with gr.Row():
                s1 = gr.Button("How does Ras regulate cell migration?", elem_classes=["suggestion-btn"])
                s2 = gr.Button("Which papers use optogenetics?", elem_classes=["suggestion-btn"])
            with gr.Row():
                s3 = gr.Button("What is the role of PTEN in chemotaxis?", elem_classes=["suggestion-btn"])
                s4 = gr.Button("Tell me about the Warburg effect", elem_classes=["suggestion-btn"])
            with gr.Row():
                s5 = gr.Button("What methods study cell polarity?", elem_classes=["suggestion-btn"])
                s6 = gr.Button("Which organisms are used in the research?", elem_classes=["suggestion-btn"])

        with gr.Column(scale=1, min_width=220):
            gr.HTML('<div class="section-label">About the Lab</div>')
            gr.HTML("""
                <div class="sidebar-panel" style="font-size:12px; color:#8a9bb8; line-height:1.6;">
                    <strong style="color:#d0ddf0;">Prof. Peter N. Devreotes</strong><br>
                    Department of Cell Biology<br>
                    Johns Hopkins School of Medicine<br><br>
                    Research spanning <strong style="color:#8ab4f8;">1975–2025</strong> focusing on
                    cell migration, chemotaxis, signal transduction, and the cytoskeleton
                    using <em>Dictyostelium discoideum</em> as a model organism.
                </div>
            """)
            gr.HTML('<div class="section-label" style="margin-top:20px;">Top Proteins</div>')
            gr.HTML("""
                <div class="sidebar-panel" style="font-size:11px; color:#8a9bb8; line-height:1.8;">
                    <span style="color:#8ab4f8;">PTEN</span> · 54 papers<br>
                    <span style="color:#8ab4f8;">cAR1</span> · 46 papers<br>
                    <span style="color:#8ab4f8;">Ras</span> · 41 papers<br>
                    <span style="color:#8ab4f8;">PI3K</span> · 26 papers<br>
                    <span style="color:#8ab4f8;">PIP3</span> · 24 papers<br>
                    <span style="color:#8ab4f8;">Gα2</span> · 24 papers<br>
                    <span style="color:#8ab4f8;">CRAC</span> · 21 papers<br>
                    <span style="color:#8ab4f8;">myosin II</span> · 19 papers
                </div>
            """)
            gr.HTML('<div class="section-label" style="margin-top:20px;">Top Concepts</div>')
            gr.HTML("""
                <div class="sidebar-panel" style="font-size:11px; color:#8a9bb8; line-height:1.8;">
                    <span style="color:#8ab4f8;">chemotaxis</span> · 149 papers<br>
                    <span style="color:#8ab4f8;">signal transduction</span> · 29 papers<br>
                    <span style="color:#8ab4f8;">cAMP signaling</span> · 27 papers<br>
                    <span style="color:#8ab4f8;">cell migration</span> · 23 papers<br>
                    <span style="color:#8ab4f8;">cell polarity</span> · 16 papers
                </div>
            """)

    def respond(message, chat_history):
        bot_response = chat_fn(message, chat_history)
        chat_history.append((message, bot_response))
        return "", chat_history, get_history_display()

    msg.submit(respond, [msg, chatbot], [msg, chatbot, history_display])
    send_btn.click(respond, [msg, chatbot], [msg, chatbot, history_display])

    for btn in [s1, s2, s3, s4, s5, s6]:
        btn.click(
            fn=lambda suggestion, h: ("", h + [(suggestion, chat_fn(suggestion, h))], get_history_display()),
            inputs=[btn, chatbot],
            outputs=[msg, chatbot, history_display],
        )

demo.launch()