import os
import re
import gradio as gr
from openai import OpenAI
from neo4j import GraphDatabase

# ---- Credentials ----
NEO4J_URI = os.environ.get("NEO4J_URI", "neo4j+s://9f45c691.databases.neo4j.io")
NEO4J_USER = os.environ.get("NEO4J_USER", "9f45c691")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "0Mnrcu_ZtECegn-6kYoRPNHvpYLBiOjNpqkygC4e9gM")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
client = OpenAI()
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


# ============================================================
# SPELLING CORRECTION
# ============================================================
def correct_spelling(question):
    """Use GPT to fix spelling errors before searching the graph."""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a spelling corrector for biology research questions. Fix any spelling errors and return ONLY the corrected question. Do not add explanation. If there are no errors, return the question unchanged. Preserve protein names, gene names, and scientific terms accurately."},
                {"role": "user", "content": question}
            ],
            temperature=0, max_tokens=150
        )
        corrected = response.choices[0].message.content.strip()
        return corrected
    except:
        return question


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
                  'know','known','like','make','makes','give','list','name',
                  'please','want','need','think','things','compare','comparison',
                  'tell','show','mentioned','mention'}
    words = re.findall(r'[a-zA-Z0-9/()-]+', question)
    return [w for w in words if w.lower() not in stop_words and len(w) > 2]


# ============================================================
# GRAPH QUERY ENGINE
# ============================================================
def query_graph(question):
    keywords = extract_keywords(question)
    question_lower = question.lower()
    context_parts = []
    seen_papers = set()

    with driver.session() as session:

        # --- Direct paper number lookup ---
        paper_numbers = re.findall(r'#(\d{1,3})\b', question)
        if not paper_numbers:
            paper_numbers = re.findall(r'paper\s+(\d{1,3})\b', question_lower)
        if paper_numbers:
            for pnum in paper_numbers[:10]:
                result = session.run(
                    "MATCH (p:Paper {number: $num}) "
                    "OPTIONAL MATCH (p)-[r1:MENTIONS_PROTEIN]->(pr:Protein) "
                    "OPTIONAL MATCH (p)-[r2:USES_METHOD]->(m:Method) "
                    "OPTIONAL MATCH (p)-[r3:EXPLORES_CONCEPT]->(c:Concept) "
                    "OPTIONAL MATCH (p)-[r4:USES_ORGANISM]->(o:Organism) "
                    "RETURN p.number AS paper_num, p.title AS title, p.year AS year, "
                    "p.key_findings AS findings, p.journal AS journal, p.authors AS authors, "
                    "collect(DISTINCT pr.name) AS proteins, "
                    "collect(DISTINCT m.name) AS methods, "
                    "collect(DISTINCT c.name) AS concepts, "
                    "collect(DISTINCT o.name) AS organisms", num=pnum)
                records = list(result)
                for r in records:
                    if r['paper_num'] not in seen_papers:
                        context_parts.append(
                            f"PAPER #{r['paper_num']} ({r['year']}): '{r['title']}'\n"
                            f"    Journal: {r['journal']}\n    Authors: {r['authors']}\n"
                            f"    Findings: {r['findings']}\n    Proteins: {r['proteins']}\n"
                            f"    Methods: {r['methods']}\n    Concepts: {r['concepts']}\n"
                            f"    Organisms: {r['organisms']}")
                        seen_papers.add(r['paper_num'])

        # --- Latest / newest ---
        if any(word in question_lower for word in ['latest', 'newest', 'recent', 'most recent']):
            result = session.run(
                "MATCH (p:Paper) WHERE p.year IS NOT NULL "
                "OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) "
                "OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method) "
                "OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) "
                "RETURN p.number AS paper_num, p.title AS title, p.year AS year, "
                "p.key_findings AS findings, "
                "collect(DISTINCT pr.name) AS proteins, "
                "collect(DISTINCT m.name) AS methods, "
                "collect(DISTINCT c.name) AS concepts "
                "ORDER BY p.year DESC LIMIT 5")
            records = list(result)
            if records:
                context_parts.append("MOST RECENT PAPERS:")
                for r in records:
                    context_parts.append(
                        f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}'\n"
                        f"    Findings: {r['findings']}\n    Proteins: {r['proteins']}\n"
                        f"    Methods: {r['methods']}\n    Concepts: {r['concepts']}")

        # --- Oldest / earliest ---
        if any(word in question_lower for word in ['oldest', 'earliest', 'first paper']):
            result = session.run(
                "MATCH (p:Paper) WHERE p.year IS NOT NULL AND p.year > 0 "
                "OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) "
                "OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) "
                "RETURN p.number AS paper_num, p.title AS title, p.year AS year, "
                "p.key_findings AS findings, "
                "collect(DISTINCT pr.name) AS proteins, "
                "collect(DISTINCT c.name) AS concepts "
                "ORDER BY p.year ASC LIMIT 5")
            records = list(result)
            if records:
                context_parts.append("EARLIEST PAPERS:")
                for r in records:
                    context_parts.append(
                        f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}'\n"
                        f"    Findings: {r['findings']}\n    Proteins: {r['proteins']}\n    Concepts: {r['concepts']}")

        # --- Year / decade filtering ---
        decade_match = re.search(r'\b(19[7-9]0|20[0-2]0)s?\b', question_lower)
        year_match = re.search(r'\b(19[7-9]\d|20[0-2]\d)\b', question)
        if decade_match:
            decade = int(decade_match.group(1)[:4])
            result = session.run(
                "MATCH (p:Paper) WHERE p.year >= $start AND p.year < $end "
                "OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) "
                "RETURN p.number AS paper_num, p.title AS title, p.year AS year, "
                "p.key_findings AS findings, collect(DISTINCT c.name) AS concepts "
                "ORDER BY p.year LIMIT 15", start=decade, end=decade+10)
            records = list(result)
            if records:
                context_parts.append(f"PAPERS FROM THE {decade}s ({len(records)} found):")
                for r in records:
                    if r['paper_num'] not in seen_papers:
                        context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}' — Concepts: {r['concepts']}")
                        seen_papers.add(r['paper_num'])
        elif year_match and not decade_match:
            year = int(year_match.group(1))
            result = session.run(
                "MATCH (p:Paper {year: $yr}) "
                "OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) "
                "RETURN p.number AS paper_num, p.title AS title, p.year AS year, "
                "p.key_findings AS findings, collect(DISTINCT c.name) AS concepts "
                "ORDER BY p.year LIMIT 15", yr=year)
            records = list(result)
            if records:
                context_parts.append(f"PAPERS FROM {year} ({len(records)} found):")
                for r in records:
                    if r['paper_num'] not in seen_papers:
                        context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}' — Concepts: {r['concepts']}")
                        seen_papers.add(r['paper_num'])

        # --- "List all" / "show all" / "top" / "most studied" / "most used" ---
        if any(phrase in question_lower for phrase in ['list all', 'show all', 'all proteins', 'all methods',
                                                        'all concepts', 'all organisms', 'top proteins',
                                                        'top methods', 'top concepts', 'most studied',
                                                        'most used', 'most common', 'most mentioned',
                                                        'most popular', 'frequently']):
            if any(w in question_lower for w in ['protein', 'gene']):
                result = session.run(
                    "MATCH (p:Paper)-[:MENTIONS_PROTEIN]->(pr:Protein) "
                    "RETURN pr.name AS name, count(p) AS papers "
                    "ORDER BY papers DESC LIMIT 20")
                records = list(result)
                if records:
                    context_parts.append("TOP PROTEINS BY PAPER COUNT:")
                    for r in records:
                        context_parts.append(f"  {r['name']}: {r['papers']} papers")
            if any(w in question_lower for w in ['method', 'technique']):
                result = session.run(
                    "MATCH (p:Paper)-[:USES_METHOD]->(m:Method) "
                    "RETURN m.name AS name, count(p) AS papers "
                    "ORDER BY papers DESC LIMIT 20")
                records = list(result)
                if records:
                    context_parts.append("TOP METHODS BY PAPER COUNT:")
                    for r in records:
                        context_parts.append(f"  {r['name']}: {r['papers']} papers")
            if any(w in question_lower for w in ['concept', 'topic', 'theme', 'subject']):
                result = session.run(
                    "MATCH (p:Paper)-[:EXPLORES_CONCEPT]->(c:Concept) "
                    "RETURN c.name AS name, count(p) AS papers "
                    "ORDER BY papers DESC LIMIT 20")
                records = list(result)
                if records:
                    context_parts.append("TOP CONCEPTS BY PAPER COUNT:")
                    for r in records:
                        context_parts.append(f"  {r['name']}: {r['papers']} papers")
            if any(w in question_lower for w in ['organism', 'species', 'model', 'cell line']):
                result = session.run(
                    "MATCH (p:Paper)-[:USES_ORGANISM]->(o:Organism) "
                    "RETURN o.name AS name, count(p) AS papers "
                    "ORDER BY papers DESC LIMIT 20")
                records = list(result)
                if records:
                    context_parts.append("TOP ORGANISMS BY PAPER COUNT:")
                    for r in records:
                        context_parts.append(f"  {r['name']}: {r['papers']} papers")
            # If no specific type matched, show all rankings
            if not any(w in question_lower for w in ['protein','gene','method','technique','concept','topic','organism','species','model','cell line']):
                for label, rel, node in [("PROTEINS","MENTIONS_PROTEIN","Protein"),
                                         ("METHODS","USES_METHOD","Method"),
                                         ("CONCEPTS","EXPLORES_CONCEPT","Concept")]:
                    result = session.run(
                        f"MATCH (p:Paper)-[:{rel}]->(n:{node}) "
                        f"RETURN n.name AS name, count(p) AS papers "
                        f"ORDER BY papers DESC LIMIT 10")
                    records = list(result)
                    if records:
                        context_parts.append(f"TOP {label}:")
                        for r in records:
                            context_parts.append(f"  {r['name']}: {r['papers']} papers")

        # --- Connection queries ("How is X connected to Y") ---
        if any(phrase in question_lower for phrase in ['connected to', 'connection between', 'related to',
                                                        'relationship between', 'link between']):
            if len(keywords) >= 2:
                kw1, kw2 = keywords[0].lower(), keywords[1].lower()
                result = session.run(
                    "MATCH (p:Paper)-[]->(e1) "
                    "WHERE toLower(e1.name) CONTAINS $k1 "
                    "WITH p "
                    "MATCH (p)-[]->(e2) "
                    "WHERE toLower(e2.name) CONTAINS $k2 "
                    "OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) "
                    "OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) "
                    "RETURN p.number AS paper_num, p.title AS title, p.year AS year, "
                    "p.key_findings AS findings, "
                    "collect(DISTINCT pr.name) AS proteins, "
                    "collect(DISTINCT c.name) AS concepts "
                    "LIMIT 10", k1=kw1, k2=kw2)
                records = list(result)
                if records:
                    context_parts.append(f"PAPERS CONNECTING '{keywords[0]}' AND '{keywords[1]}':")
                    for r in records:
                        if r['paper_num'] not in seen_papers:
                            context_parts.append(
                                f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}'\n"
                                f"    Findings: {r['findings']}\n    Proteins: {r['proteins']}\n    Concepts: {r['concepts']}")
                            seen_papers.add(r['paper_num'])

        # --- Keyword search across all entity types ---
        for kw in keywords:
            kw_lower = kw.lower()
            search_terms = [kw_lower]
            if len(kw_lower) >= 5:
                search_terms.append(kw_lower[1:])
                search_terms.append(kw_lower[:-1])

            for term in search_terms:
                found = False

                result = session.run(
                    "MATCH (p:Paper)-[r:MENTIONS_PROTEIN]->(pr:Protein) "
                    "WHERE toLower(pr.name) CONTAINS $q "
                    "RETURN p.number AS paper_num, p.title AS title, p.year AS year, "
                    "pr.name AS protein, r.context AS context "
                    "ORDER BY p.year DESC LIMIT 10", q=term)
                records = list(result)
                if records:
                    context_parts.append(f"PROTEIN MATCHES for '{kw}':")
                    for r in records:
                        if r['paper_num'] not in seen_papers:
                            context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}' mentions {r['protein']} — {r['context']}")
                            seen_papers.add(r['paper_num'])
                    found = True
                    break

                result = session.run(
                    "MATCH (p:Paper)-[r:USES_METHOD]->(m:Method) "
                    "WHERE toLower(m.name) CONTAINS $q "
                    "RETURN p.number AS paper_num, p.title AS title, p.year AS year, "
                    "m.name AS method, r.context AS context "
                    "ORDER BY p.year DESC LIMIT 10", q=term)
                records = list(result)
                if records:
                    context_parts.append(f"\nMETHOD MATCHES for '{kw}':")
                    for r in records:
                        if r['paper_num'] not in seen_papers:
                            context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}' uses {r['method']} — {r['context']}")
                            seen_papers.add(r['paper_num'])
                    found = True
                    break

                result = session.run(
                    "MATCH (p:Paper)-[r:EXPLORES_CONCEPT]->(c:Concept) "
                    "WHERE toLower(c.name) CONTAINS $q "
                    "RETURN p.number AS paper_num, p.title AS title, p.year AS year, "
                    "c.name AS concept, r.context AS context "
                    "ORDER BY p.year DESC LIMIT 10", q=term)
                records = list(result)
                if records:
                    context_parts.append(f"\nCONCEPT MATCHES for '{kw}':")
                    for r in records:
                        if r['paper_num'] not in seen_papers:
                            context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}' explores {r['concept']} — {r['context']}")
                            seen_papers.add(r['paper_num'])
                    found = True
                    break

                result = session.run(
                    "MATCH (p:Paper)-[r:USES_ORGANISM]->(o:Organism) "
                    "WHERE toLower(o.name) CONTAINS $q "
                    "RETURN p.number AS paper_num, p.title AS title, p.year AS year, "
                    "o.name AS organism, r.context AS context "
                    "ORDER BY p.year DESC LIMIT 10", q=term)
                records = list(result)
                if records:
                    context_parts.append(f"\nORGANISM MATCHES for '{kw}':")
                    for r in records:
                        if r['paper_num'] not in seen_papers:
                            context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}' uses {r['organism']} — {r['context']}")
                            seen_papers.add(r['paper_num'])
                    found = True
                    break

                if found:
                    break

        # --- Title and findings search ---
        for kw in keywords:
            result = session.run(
                "MATCH (p:Paper) "
                "WHERE toLower(p.title) CONTAINS $q OR toLower(p.key_findings) CONTAINS $q "
                "OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) "
                "OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method) "
                "OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) "
                "RETURN p.number AS paper_num, p.title AS title, p.year AS year, "
                "p.key_findings AS findings, "
                "collect(DISTINCT pr.name) AS proteins, "
                "collect(DISTINCT m.name) AS methods, "
                "collect(DISTINCT c.name) AS concepts "
                "LIMIT 5", q=kw.lower())
            records = list(result)
            if records:
                new_records = [r for r in records if r['paper_num'] not in seen_papers]
                if new_records:
                    context_parts.append(f"\nPAPER MATCHES for '{kw}':")
                    for r in new_records:
                        context_parts.append(
                            f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}'\n"
                            f"    Findings: {r['findings']}\n    Proteins: {r['proteins']}\n    Methods: {r['methods']}")
                        seen_papers.add(r['paper_num'])

        # --- Graph statistics ---
        if any(word in question_lower for word in ['how many', 'total', 'overview', 'statistics', 'stat']):
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
            result = session.run("MATCH ()-[r]->() RETURN count(r) AS count")
            rc = result.single()["count"]
            context_parts.append(
                f"\nGRAPH STATISTICS:\n  Total papers: {pc}\n  Total proteins: {prc}\n"
                f"  Total methods: {mc}\n  Total concepts: {cc}\n  Total organisms: {oc}\n"
                f"  Total relationships: {rc}")

    if not context_parts:
        return "NO_RESULTS"
    return "\n".join(context_parts)


# ============================================================
# SYSTEM PROMPT
# ============================================================
SYSTEM_PROMPT = """You are the Devreotes Lab Research Assistant, a specialized AI for Professor Peter Devreotes' lab at Johns Hopkins University School of Medicine.

The knowledge graph contains 238 papers (1975-2025), 738 proteins, 531 methods, 329 concepts, and 225 organisms connected by 3,649 relationships.

RULES:
1. Answer in your own words from the provided graph context. Synthesize naturally.
2. Keep responses concise — 2-4 sentences for simple questions, more only when necessary.
3. Always cite paper numbers (e.g., "Paper #276").
4. If the information is not in the graph context or conversation history, say: "This information is not available in the Devreotes Lab knowledge base."
5. NEVER suggest topics or information outside the knowledge base.
6. When listing papers, keep descriptions brief — title, year, and one-line relevance.
7. Highlight connections between papers when relevant.
8. If a follow-up references something already discussed, answer from conversation history.
9. If asked to summarize a previous answer, rewrite concisely without adding new information.
"""

GREETING_WORDS = {'hi','hello','hey','good morning','good afternoon','good evening',
                  'howdy','greetings','sup','yo',"what's up",'whats up','hola'}


# ============================================================
# ANSWER GENERATION
# ============================================================
def generate_answer(question, conversation_history=None):
    if not question or not question.strip():
        return "Please type a question to get started."

    question_stripped = question.strip().lower().rstrip('!?.,:;')

    # Greetings
    if question_stripped in GREETING_WORDS or any(question_stripped.startswith(g) for g in GREETING_WORDS):
        return ("Hello! I'm the Devreotes Lab Research Assistant — ask me anything about "
                "Professor Devreotes' 238 published papers on cell migration, chemotaxis, "
                "and signal transduction.")

    # Identity questions
    off_topic_triggers = ['how are you','who are you','what are you','your name','what can you do','help me']
    if any(trigger in question_stripped for trigger in off_topic_triggers):
        return ("I'm the Devreotes Lab Research Assistant. I can answer questions about "
                "proteins, methods, concepts, and organisms from 238 research papers "
                "spanning 1975 to 2025.")

    # Thanks / bye
    if 'thank' in question_stripped:
        return "You're welcome! Feel free to ask more."
    if 'bye' in question_stripped or 'goodbye' in question_stripped:
        return "Goodbye! Come back anytime."

    # Build conversation history for GPT
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if conversation_history:
        for turn in conversation_history[-8:]:
            messages.append(turn)

    # Check if this is a conversational follow-up
    is_conversational = False
    if conversation_history:
        ref_words = ['this','these','those','them','they','it','that','above',
                     'previous','mentioned','you said','you mentioned','the same',
                     'where do i find','when was','when were','who wrote','which year',
                     'how many of','more about','tell me more','elaborate',
                     'go on','continue','what else','which ones','specifically',
                     'in detail','expand on']
        summary_words = ['summarize','summary','in short','briefly','define this',
                         'in a summary','short version','simplify','simpler terms',
                         'in a nutshell','quick summary','shorter']

        if any(word in question_stripped for word in summary_words):
            is_conversational = True
        elif len(question.split()) <= 8 and any(word in question_stripped for word in ref_words):
            is_conversational = True

    if is_conversational:
        messages.append({"role": "user", "content": question})
        response = client.chat.completions.create(
            model="gpt-4o-mini", messages=messages, temperature=0.3, max_tokens=800)
        return response.choices[0].message.content

    # Correct spelling before searching
    corrected = correct_spelling(question)

    # Search the graph
    graph_context = query_graph(corrected)

    # If no results, try with conversation history
    if graph_context == "NO_RESULTS":
        if conversation_history:
            messages.append({"role": "user", "content": question})
            response = client.chat.completions.create(
                model="gpt-4o-mini", messages=messages, temperature=0.3, max_tokens=800)
            answer = response.choices[0].message.content
            if "not available" not in answer.lower() and "don't have" not in answer.lower():
                return answer
        return "This information is not available in the Devreotes Lab knowledge base."

    # Send graph context + question to GPT
    messages.append({"role": "user", "content":
        f"Based on the following knowledge graph data, answer the question.\n\n"
        f"GRAPH CONTEXT:\n{graph_context}\n\nQUESTION: {question}"})

    response = client.chat.completions.create(
        model="gpt-4o-mini", messages=messages, temperature=0.3, max_tokens=800)
    return response.choices[0].message.content


# ============================================================
# SEARCH HISTORY (cached answers)
# ============================================================
search_history = {}
history_order = []


def chat_fn(message, history):
    global search_history, history_order
    conversation_history = []
    for h in history:
        if isinstance(h, dict):
            conversation_history.append(h)

    answer = generate_answer(message, conversation_history)

    skip = ['hi','hello','hey','thanks','thank you','bye','goodbye','']
    if message.strip().lower() not in skip:
        search_history[message] = answer
        if message in history_order:
            history_order.remove(message)
        history_order.insert(0, message)
        history_order[:] = history_order[:20]

    return answer


def get_visible_history(show_all=False):
    limit = 15 if show_all else 5
    return history_order[:limit]


# ============================================================
# GRADIO UI
# ============================================================
with gr.Blocks(title="Devreotes Lab Research Assistant") as demo:

    # ---- HEADER ----
    gr.HTML("""
        <div style="background: linear-gradient(135deg, #0a1628 0%, #1a3050 40%, #0f2440 70%, #0a1628 100%);
                    padding: 24px 32px; border-bottom: 2px solid rgba(79,172,254,0.2);
                    display: flex; align-items: center; gap: 20px; position: relative; overflow: hidden;">
            <div style="position:absolute; top:0; left:0; right:0; bottom:0;
                        background: radial-gradient(ellipse at 20% 50%, rgba(79,172,254,0.08) 0%, transparent 60%),
                                    radial-gradient(ellipse at 80% 50%, rgba(120,200,255,0.05) 0%, transparent 50%);
                        pointer-events: none;"></div>
            <div style="position: relative; display: flex; align-items: center; gap: 6px;">
                <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
                    <circle cx="24" cy="24" r="22" stroke="#4facfe" stroke-width="1.5" opacity="0.3"/>
                    <circle cx="24" cy="24" r="16" stroke="#4facfe" stroke-width="1" opacity="0.2"/>
                    <circle cx="24" cy="24" r="4" fill="#4facfe" opacity="0.8"/>
                    <circle cx="24" cy="12" r="2.5" fill="#8ab4f8" opacity="0.6"/>
                    <circle cx="34" cy="20" r="2.5" fill="#8ab4f8" opacity="0.6"/>
                    <circle cx="34" cy="30" r="2.5" fill="#8ab4f8" opacity="0.6"/>
                    <circle cx="24" cy="36" r="2.5" fill="#8ab4f8" opacity="0.6"/>
                    <circle cx="14" cy="30" r="2.5" fill="#8ab4f8" opacity="0.6"/>
                    <circle cx="14" cy="20" r="2.5" fill="#8ab4f8" opacity="0.6"/>
                    <line x1="24" y1="24" x2="24" y2="12" stroke="#4facfe" stroke-width="0.8" opacity="0.4"/>
                    <line x1="24" y1="24" x2="34" y2="20" stroke="#4facfe" stroke-width="0.8" opacity="0.4"/>
                    <line x1="24" y1="24" x2="34" y2="30" stroke="#4facfe" stroke-width="0.8" opacity="0.4"/>
                    <line x1="24" y1="24" x2="24" y2="36" stroke="#4facfe" stroke-width="0.8" opacity="0.4"/>
                    <line x1="24" y1="24" x2="14" y2="30" stroke="#4facfe" stroke-width="0.8" opacity="0.4"/>
                    <line x1="24" y1="24" x2="14" y2="20" stroke="#4facfe" stroke-width="0.8" opacity="0.4"/>
                </svg>
            </div>
            <div style="position: relative;">
                <div style="font-size: 22px; font-weight: 700; color: #e8f0fe;
                            letter-spacing: -0.5px; font-family: 'Georgia', serif;">
                    Devreotes Lab Research Assistant
                </div>
                <div style="font-size: 12px; color: #4facfe; font-weight: 500;
                            letter-spacing: 1px; margin-top: 2px; text-transform: uppercase;">
                    GraphRAG &middot; 238 Papers &middot; Johns Hopkins University School of Medicine
                </div>
            </div>
        </div>
    """)

    with gr.Row():
        # ---- LEFT SIDEBAR ----
        with gr.Column(scale=1, min_width=200):
            gr.HTML("""
                <div style="padding: 12px;">
                    <div style="color: #4facfe; font-size: 11px; font-weight: 600;
                                text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 12px;">
                        Knowledge Graph
                    </div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px;">
                        <div style="background: #111d30; border: 1px solid rgba(79,172,254,0.12);
                                    border-radius: 10px; padding: 12px; text-align: center;">
                            <div style="font-size: 22px; font-weight: 700; color: #4facfe;">238</div>
                            <div style="font-size: 9px; color: #5a6d85; text-transform: uppercase; letter-spacing: 1px;">Papers</div>
                        </div>
                        <div style="background: #111d30; border: 1px solid rgba(79,172,254,0.12);
                                    border-radius: 10px; padding: 12px; text-align: center;">
                            <div style="font-size: 22px; font-weight: 700; color: #4facfe;">738</div>
                            <div style="font-size: 9px; color: #5a6d85; text-transform: uppercase; letter-spacing: 1px;">Proteins</div>
                        </div>
                        <div style="background: #111d30; border: 1px solid rgba(79,172,254,0.12);
                                    border-radius: 10px; padding: 12px; text-align: center;">
                            <div style="font-size: 22px; font-weight: 700; color: #4facfe;">531</div>
                            <div style="font-size: 9px; color: #5a6d85; text-transform: uppercase; letter-spacing: 1px;">Methods</div>
                        </div>
                        <div style="background: #111d30; border: 1px solid rgba(79,172,254,0.12);
                                    border-radius: 10px; padding: 12px; text-align: center;">
                            <div style="font-size: 22px; font-weight: 700; color: #4facfe;">329</div>
                            <div style="font-size: 9px; color: #5a6d85; text-transform: uppercase; letter-spacing: 1px;">Concepts</div>
                        </div>
                    </div>
                    <div style="color: #4facfe; font-size: 11px; font-weight: 600;
                                text-transform: uppercase; letter-spacing: 1.5px; margin-top: 20px; margin-bottom: 8px;">
                        Recent Searches
                    </div>
                </div>
            """)
            history_dropdown = gr.Dropdown(choices=[], label="Click to replay a question",
                                           interactive=True, allow_custom_value=False)
            show_more_state = gr.State(False)

        # ---- CENTER CHAT ----
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(height=520, show_label=False)
            with gr.Row():
                msg = gr.Textbox(placeholder="Ask about the Devreotes Lab research...",
                                 show_label=False, scale=6)
                send_btn = gr.Button("Send", scale=1, variant="primary")

            gr.HTML("""
                <div style="color: #4facfe; font-size: 11px; font-weight: 600;
                            text-transform: uppercase; letter-spacing: 1.5px; margin-top: 12px; margin-bottom: 8px;">
                    Suggested Questions
                </div>
            """)
            with gr.Row():
                s1 = gr.Button("How does Ras regulate cell migration?", size="sm")
                s2 = gr.Button("Which papers use optogenetics?", size="sm")
            with gr.Row():
                s3 = gr.Button("What is the role of PTEN in chemotaxis?", size="sm")
                s4 = gr.Button("Tell me about the Warburg effect", size="sm")
            with gr.Row():
                s5 = gr.Button("What methods study cell polarity?", size="sm")
                s6 = gr.Button("Papers from the 2020s", size="sm")

        # ---- RIGHT SIDEBAR ----
        with gr.Column(scale=1, min_width=200):
            gr.HTML("""
                <div style="padding: 12px;">
                    <div style="color: #4facfe; font-size: 11px; font-weight: 600;
                                text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 12px;">
                        About the Lab
                    </div>
                    <div style="background: #111d30; border: 1px solid rgba(79,172,254,0.12);
                                border-radius: 12px; padding: 16px; font-size: 12px; color: #8a9bb8; line-height: 1.6;">
                        <strong style="color: #d0ddf0;">Prof. Peter N. Devreotes</strong><br>
                        Department of Cell Biology<br>
                        Johns Hopkins School of Medicine<br><br>
                        Research spanning <strong style="color: #8ab4f8;">1975 &ndash; 2025</strong> focusing on
                        cell migration, chemotaxis, signal transduction, and the cytoskeleton.
                    </div>
                    <div style="color: #4facfe; font-size: 11px; font-weight: 600;
                                text-transform: uppercase; letter-spacing: 1.5px; margin-top: 20px; margin-bottom: 8px;">
                        Top Proteins
                    </div>
                    <div style="background: #111d30; border: 1px solid rgba(79,172,254,0.12);
                                border-radius: 12px; padding: 16px; font-size: 11px; color: #8a9bb8; line-height: 1.8;">
                        <span style="color: #8ab4f8;">PTEN</span> &middot; 54 papers<br>
                        <span style="color: #8ab4f8;">cAR1</span> &middot; 46 papers<br>
                        <span style="color: #8ab4f8;">Ras</span> &middot; 41 papers<br>
                        <span style="color: #8ab4f8;">PI3K</span> &middot; 26 papers<br>
                        <span style="color: #8ab4f8;">PIP3</span> &middot; 24 papers<br>
                        <span style="color: #8ab4f8;">myosin II</span> &middot; 19 papers
                    </div>
                    <div style="color: #4facfe; font-size: 11px; font-weight: 600;
                                text-transform: uppercase; letter-spacing: 1.5px; margin-top: 20px; margin-bottom: 8px;">
                        Top Concepts
                    </div>
                    <div style="background: #111d30; border: 1px solid rgba(79,172,254,0.12);
                                border-radius: 12px; padding: 16px; font-size: 11px; color: #8a9bb8; line-height: 1.8;">
                        <span style="color: #8ab4f8;">chemotaxis</span> &middot; 149 papers<br>
                        <span style="color: #8ab4f8;">signal transduction</span> &middot; 29 papers<br>
                        <span style="color: #8ab4f8;">cAMP signaling</span> &middot; 27 papers<br>
                        <span style="color: #8ab4f8;">cell migration</span> &middot; 23 papers<br>
                        <span style="color: #8ab4f8;">cell polarity</span> &middot; 16 papers
                    </div>
                </div>
            """)

    # ---- EVENT HANDLERS ----
    def respond(message, chat_history):
        bot_response = chat_fn(message, chat_history)
        chat_history.append({"role": "user", "content": message})
        chat_history.append({"role": "assistant", "content": bot_response})
        return "", chat_history, gr.update(choices=get_visible_history(), value=None)

    msg.submit(respond, [msg, chatbot], [msg, chatbot, history_dropdown])
    send_btn.click(respond, [msg, chatbot], [msg, chatbot, history_dropdown])

    for btn in [s1, s2, s3, s4, s5, s6]:
        btn.click(
            fn=lambda suggestion, h: (
                "",
                h + [{"role": "user", "content": suggestion},
                     {"role": "assistant", "content": chat_fn(suggestion, h)}],
                gr.update(choices=get_visible_history(), value=None)
            ),
            inputs=[btn, chatbot],
            outputs=[msg, chatbot, history_dropdown],
        )

    def replay_history(selected, chat_history):
        if selected and selected in search_history:
            chat_history.append({"role": "user", "content": selected})
            chat_history.append({"role": "assistant", "content": search_history[selected]})
        return chat_history, gr.update(value=None)

    history_dropdown.change(replay_history, [history_dropdown, chatbot], [chatbot, history_dropdown])

demo.launch()