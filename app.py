import os
import re
import json
import tempfile
import fitz
import gradio as gr
from openai import OpenAI
from neo4j import GraphDatabase

NEO4J_URI = os.environ.get("NEO4J_URI", "")
NEO4J_USER = os.environ.get("NEO4J_USER", "")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
UPLOAD_PASSWORD = os.environ.get("UPLOAD_PASSWORD", "")

os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
client = OpenAI()
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


# ============================================================
# VOICE TRANSCRIPTION (Whisper)
# ============================================================
def transcribe_audio(audio_path):
    """Transcribe audio file using OpenAI Whisper API. Returns (text, language)."""
    if not audio_path:
        return "", "en"
    try:
        with open(audio_path, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="verbose_json"
            )
        text = response.text.strip()
        language = getattr(response, 'language', 'en') or 'en'
        return text, language
    except Exception as e:
        return f"[Voice transcription failed: {str(e)}]", "en"


# ============================================================
# LANGUAGE DETECTION
# ============================================================
def detect_language(text):
    """Detect if text is French or English."""
    french_words = {'le','la','les','de','du','des','un','une','et','est','en','que',
                    'qui','dans','pour','sur','avec','ce','cette','sont','par','au',
                    'aux','ou','mais','pas','ne','ont','fait','plus','quels','quelle',
                    'quelles','quel','comment','quoi','pourquoi','merci','bonjour',
                    'salut','papiers','protéines','méthodes','recherche','cellulaire',
                    'migration','résultats','connexion','entre'}
    words = text.lower().split()
    french_count = sum(1 for w in words if w in french_words)
    return "fr" if french_count >= 2 or any(w in text.lower() for w in ['bonjour','salut','merci','s\'il vous']) else "en"


FRENCH_GREETINGS = {'bonjour','salut','bonsoir','coucou','hello','bonne journée'}


def correct_spelling(question):
    try:
        response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "system", "content": "You are a spelling corrector for biology research questions in English or French. Fix any spelling errors and return ONLY the corrected question. If there are no errors, return the question unchanged."}, {"role": "user", "content": question}], temperature=0, max_tokens=150)
        return response.choices[0].message.content.strip()
    except:
        return question


def extract_keywords(question):
    stop_words = {'what','which','where','when','how','does','about','tell','show','find','papers','paper','that','this','with','from','have','been','their','they','there','were','using','used','use','the','and','for','are','can','you','me','is','it','in','of','to','do','a','an','on','by','has','was','be','or','at','its','who','did','will','would','could','should','between','through','role','most','many','some','any','all','more','than','these','those','into','also','but','not','only','such','other','each','every','much','study','studies','research','describe','explain','discuss','related','involved','connection','connected','relationship','know','known','like','make','makes','give','list','name','please','want','need','think','things','compare','comparison','tell','show','mentioned','mention',
                  'le','la','les','de','du','des','un','une','et','est','en','que','qui','dans','pour','sur','avec','ce','cette','sont','par','au','aux','ou','mais','pas','ne','ont','fait','plus','quels','quelle','quelles','quel','comment','quoi','pourquoi'}
    words = re.findall(r'[a-zA-Z0-9/()-éèêëàâùûîïôç]+', question)
    return [w for w in words if w.lower() not in stop_words and len(w) > 2]


def query_graph(question):
    keywords = extract_keywords(question)
    question_lower = question.lower()
    context_parts = []
    seen_papers = set()
    with driver.session() as session:
        paper_numbers = re.findall(r'#(\d{1,3})\b', question)
        if not paper_numbers:
            paper_numbers = re.findall(r'paper\s+(\d{1,3})\b', question_lower)
            if not paper_numbers:
                paper_numbers = re.findall(r'papier\s+(\d{1,3})\b', question_lower)
                if not paper_numbers:
                    paper_numbers = re.findall(r'article\s+(\d{1,3})\b', question_lower)
        if paper_numbers:
            for pnum in paper_numbers[:10]:
                result = session.run("MATCH (p:Paper {number: $num}) OPTIONAL MATCH (p)-[r1:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[r2:USES_METHOD]->(m:Method) OPTIONAL MATCH (p)-[r3:EXPLORES_CONCEPT]->(c:Concept) OPTIONAL MATCH (p)-[r4:USES_ORGANISM]->(o:Organism) RETURN p.number AS paper_num, p.title AS title, p.year AS year, p.key_findings AS findings, p.journal AS journal, p.authors AS authors, collect(DISTINCT pr.name) AS proteins, collect(DISTINCT m.name) AS methods, collect(DISTINCT c.name) AS concepts, collect(DISTINCT o.name) AS organisms", num=pnum)
                for r in result:
                    if r['paper_num'] not in seen_papers:
                        context_parts.append(f"PAPER #{r['paper_num']} ({r['year']}): '{r['title']}'\n    Journal: {r['journal']}\n    Authors: {r['authors']}\n    Findings: {r['findings']}\n    Proteins: {r['proteins']}\n    Methods: {r['methods']}\n    Concepts: {r['concepts']}\n    Organisms: {r['organisms']}")
                        seen_papers.add(r['paper_num'])
        if any(word in question_lower for word in ['latest', 'newest', 'recent', 'most recent', 'récent', 'dernier', 'dernière', 'plus récent']):
            result = session.run("MATCH (p:Paper) WHERE p.year IS NOT NULL OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS paper_num, p.title AS title, p.year AS year, p.key_findings AS findings, collect(DISTINCT pr.name) AS proteins, collect(DISTINCT m.name) AS methods, collect(DISTINCT c.name) AS concepts ORDER BY p.year DESC LIMIT 5")
            for r in result:
                context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}'\n    Findings: {r['findings']}\n    Proteins: {r['proteins']}\n    Methods: {r['methods']}\n    Concepts: {r['concepts']}")
        if any(word in question_lower for word in ['oldest', 'earliest', 'first paper', 'premier', 'ancien', 'plus ancien']):
            result = session.run("MATCH (p:Paper) WHERE p.year IS NOT NULL AND p.year > 0 OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS paper_num, p.title AS title, p.year AS year, p.key_findings AS findings, collect(DISTINCT pr.name) AS proteins, collect(DISTINCT c.name) AS concepts ORDER BY p.year ASC LIMIT 5")
            for r in result:
                context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}'\n    Findings: {r['findings']}\n    Proteins: {r['proteins']}\n    Concepts: {r['concepts']}")
        decade_match = re.search(r'\b(19[7-9]0|20[0-2]0)s?\b', question_lower)
        year_match = re.search(r'\b(19[7-9]\d|20[0-2]\d)\b', question)
        if decade_match:
            decade = int(decade_match.group(1)[:4])
            result = session.run("MATCH (p:Paper) WHERE p.year >= $start AND p.year < $end OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS paper_num, p.title AS title, p.year AS year, p.key_findings AS findings, collect(DISTINCT c.name) AS concepts ORDER BY p.year LIMIT 15", start=decade, end=decade+10)
            records = list(result)
            if records:
                context_parts.append(f"PAPERS FROM THE {decade}s ({len(records)} found):")
                for r in records:
                    if r['paper_num'] not in seen_papers:
                        context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}' — Concepts: {r['concepts']}")
                        seen_papers.add(r['paper_num'])
        elif year_match and not decade_match:
            year = int(year_match.group(1))
            result = session.run("MATCH (p:Paper {year: $yr}) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS paper_num, p.title AS title, p.year AS year, p.key_findings AS findings, collect(DISTINCT c.name) AS concepts ORDER BY p.year LIMIT 15", yr=year)
            records = list(result)
            if records:
                context_parts.append(f"PAPERS FROM {year} ({len(records)} found):")
                for r in records:
                    if r['paper_num'] not in seen_papers:
                        context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}' — Concepts: {r['concepts']}")
                        seen_papers.add(r['paper_num'])
        if any(phrase in question_lower for phrase in ['list all', 'show all', 'all proteins', 'all methods', 'all concepts', 'all organisms', 'all genes', 'top proteins', 'top methods', 'top concepts', 'top genes', 'most studied', 'most used', 'most common', 'most mentioned', 'frequently', 'toutes les', 'tous les', 'plus étudié']):
            if any(w in question_lower for w in ['protein', 'gene', 'protéine', 'gène']):
                result = session.run("MATCH (p:Paper)-[:MENTIONS_PROTEIN]->(pr:Protein) RETURN pr.name AS name, count(p) AS papers ORDER BY papers DESC LIMIT 20")
                records = list(result)
                if records:
                    context_parts.append("TOP PROTEINS/GENES BY PAPER COUNT:")
                    for r in records:
                        context_parts.append(f"  {r['name']}: {r['papers']} papers")
            elif any(w in question_lower for w in ['method', 'technique', 'méthode']):
                result = session.run("MATCH (p:Paper)-[:USES_METHOD]->(m:Method) RETURN m.name AS name, count(p) AS papers ORDER BY papers DESC LIMIT 20")
                records = list(result)
                if records:
                    context_parts.append("TOP METHODS:")
                    for r in records:
                        context_parts.append(f"  {r['name']}: {r['papers']} papers")
            elif any(w in question_lower for w in ['concept', 'topic', 'theme', 'sujet']):
                result = session.run("MATCH (p:Paper)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN c.name AS name, count(p) AS papers ORDER BY papers DESC LIMIT 20")
                records = list(result)
                if records:
                    context_parts.append("TOP CONCEPTS:")
                    for r in records:
                        context_parts.append(f"  {r['name']}: {r['papers']} papers")
            elif any(w in question_lower for w in ['organism', 'species', 'model', 'organisme', 'espèce']):
                result = session.run("MATCH (p:Paper)-[:USES_ORGANISM]->(o:Organism) RETURN o.name AS name, count(p) AS papers ORDER BY papers DESC LIMIT 20")
                records = list(result)
                if records:
                    context_parts.append("TOP ORGANISMS:")
                    for r in records:
                        context_parts.append(f"  {r['name']}: {r['papers']} papers")
            else:
                for label, rel, node in [("PROTEINS","MENTIONS_PROTEIN","Protein"),("METHODS","USES_METHOD","Method"),("CONCEPTS","EXPLORES_CONCEPT","Concept")]:
                    result = session.run(f"MATCH (p:Paper)-[:{rel}]->(n:{node}) RETURN n.name AS name, count(p) AS papers ORDER BY papers DESC LIMIT 10")
                    records = list(result)
                    if records:
                        context_parts.append(f"TOP {label}:")
                        for r in records:
                            context_parts.append(f"  {r['name']}: {r['papers']} papers")
        if any(phrase in question_lower for phrase in ['connected to', 'connection between', 'related to', 'relationship between', 'link between', 'lien entre', 'connexion entre', 'relié à', 'rapport entre']):
            if len(keywords) >= 2:
                kw1, kw2 = keywords[0].lower(), keywords[1].lower()
                result = session.run("MATCH (p:Paper)-[]->(e1) WHERE toLower(e1.name) CONTAINS $k1 WITH p MATCH (p)-[]->(e2) WHERE toLower(e2.name) CONTAINS $k2 OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS paper_num, p.title AS title, p.year AS year, p.key_findings AS findings, collect(DISTINCT pr.name) AS proteins, collect(DISTINCT c.name) AS concepts LIMIT 10", k1=kw1, k2=kw2)
                records = list(result)
                if records:
                    context_parts.append(f"PAPERS CONNECTING '{keywords[0]}' AND '{keywords[1]}':")
                    for r in records:
                        if r['paper_num'] not in seen_papers:
                            context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}'\n    Findings: {r['findings']}\n    Proteins: {r['proteins']}\n    Concepts: {r['concepts']}")
                            seen_papers.add(r['paper_num'])
        for kw in keywords:
            kw_lower = kw.lower()
            search_terms = [kw_lower]
            if len(kw_lower) >= 5:
                search_terms.extend([kw_lower[1:], kw_lower[:-1]])
            for term in search_terms:
                found = False
                for rel, node, label in [("MENTIONS_PROTEIN","Protein","protein"),("MENTIONS_GENE","Gene","gene"),("USES_METHOD","Method","method"),("EXPLORES_CONCEPT","Concept","concept"),("USES_ORGANISM","Organism","organism")]:
                    result = session.run(f"MATCH (p:Paper)-[r:{rel}]->(n:{node}) WHERE toLower(n.name) CONTAINS $q RETURN p.number AS paper_num, p.title AS title, p.year AS year, n.name AS entity, r.context AS context ORDER BY p.year DESC LIMIT 10", q=term)
                    records = list(result)
                    if records:
                        context_parts.append(f"\n{label.upper()} MATCHES for '{kw}':")
                        for r in records:
                            if r['paper_num'] not in seen_papers:
                                context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}' — {r['entity']}: {r['context']}")
                                seen_papers.add(r['paper_num'])
                        found = True
                        break
                if found:
                    break
        for kw in keywords:
            result = session.run("MATCH (p:Paper) WHERE toLower(p.title) CONTAINS $q OR toLower(p.key_findings) CONTAINS $q OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS paper_num, p.title AS title, p.year AS year, p.key_findings AS findings, collect(DISTINCT pr.name) AS proteins, collect(DISTINCT m.name) AS methods, collect(DISTINCT c.name) AS concepts LIMIT 5", q=kw.lower())
            records = list(result)
            if records:
                new_records = [r for r in records if r['paper_num'] not in seen_papers]
                if new_records:
                    context_parts.append(f"\nPAPER MATCHES for '{kw}':")
                    for r in new_records:
                        context_parts.append(f"  Paper #{r['paper_num']} ({r['year']}): '{r['title']}'\n    Findings: {r['findings']}\n    Proteins: {r['proteins']}\n    Methods: {r['methods']}")
                        seen_papers.add(r['paper_num'])
        if any(word in question_lower for word in ['how many', 'total', 'overview', 'statistics', 'stat', 'combien', 'statistiques', 'aperçu']):
            counts = {}
            for label in ['Paper', 'Protein', 'Gene', 'Method', 'Concept', 'Organism']:
                result = session.run(f"MATCH (n:{label}) RETURN count(n) AS count")
                counts[label] = result.single()["count"]
            result = session.run("MATCH ()-[r]->() RETURN count(r) AS count")
            counts['Relationships'] = result.single()["count"]
            context_parts.append(f"\nGRAPH STATISTICS:\n  Papers: {counts['Paper']}\n  Proteins: {counts['Protein']}\n  Genes: {counts['Gene']}\n  Methods: {counts['Method']}\n  Concepts: {counts['Concept']}\n  Organisms: {counts['Organism']}\n  Relationships: {counts['Relationships']}")
    return "\n".join(context_parts) if context_parts else "NO_RESULTS"


SYSTEM_PROMPT = """You are the Devreotes Lab Research Assistant, a specialized AI for Professor Peter Devreotes' lab at Johns Hopkins University School of Medicine.
The knowledge graph contains 239 papers (1975-2025), 738 proteins/genes, 531 methods, 330 concepts, and 228 organisms connected by thousands of relationships.

CRITICAL LANGUAGE RULE: Always respond in the SAME LANGUAGE as the user's question. If the user asks in French, respond entirely in French. If the user asks in English, respond in English.

RULES:
1. Answer in your own words from the provided graph context. Synthesize naturally.
2. Keep responses concise - 2-4 sentences for simple questions, more only when necessary.
3. Always cite paper numbers (e.g., "Paper #276" or "Article #276" in French).
4. If the information is not in the graph context or conversation history, say: "This information is not available in the Devreotes Lab knowledge base." (or the French equivalent if asked in French).
5. ALWAYS cite specific papers from the graph context. Never give generic textbook definitions without paper citations.
6. NEVER suggest topics or information outside the knowledge base.
7. When listing papers, keep descriptions brief.
8. Highlight connections between papers when relevant.
9. If a follow-up references something already discussed, answer from conversation history.
10. If asked to summarize a previous answer, rewrite concisely without adding new information."""

GREETING_WORDS = {'hi','hello','hey','good morning','good afternoon','good evening','howdy','greetings','sup','yo',"what's up",'whats up','hola'}

GREETING_EN = "Hello! I'm the Devreotes Lab Research Assistant — ask me anything about Professor Devreotes' 239 published papers on cell migration, chemotaxis, and signal transduction."
GREETING_FR = "Bonjour ! Je suis l'assistant de recherche du laboratoire Devreotes — posez-moi n'importe quelle question sur les 239 articles publiés par le professeur Devreotes sur la migration cellulaire, la chimiotaxie et la transduction du signal."
NOT_AVAILABLE_FR = "Cette information n'est pas disponible dans la base de connaissances du laboratoire Devreotes."


def generate_answer(question, conversation_history=None, lang="en"):
    if not question or not question.strip():
        return "Please type a question to get started." if lang == "en" else "Veuillez taper une question pour commencer."
    question_stripped = question.strip().lower().rstrip('!?.,:;')

    # Greetings
    if question_stripped in GREETING_WORDS or any(question_stripped.startswith(g) for g in GREETING_WORDS):
        return GREETING_EN
    if question_stripped in FRENCH_GREETINGS or any(question_stripped.startswith(g) for g in FRENCH_GREETINGS):
        return GREETING_FR

    off_topic_triggers = ['how are you','who are you','what are you','your name','what can you do','help me','comment allez','qui êtes','aide']
    if any(trigger in question_stripped for trigger in off_topic_triggers):
        if lang == "fr":
            return "Je suis l'assistant de recherche du laboratoire Devreotes. Je peux répondre à vos questions sur les protéines, gènes, méthodes, concepts et organismes issus de 238 articles de recherche (1975-2025)."
        return "I'm the Devreotes Lab Research Assistant. I can answer questions about proteins, genes, methods, concepts, and organisms from 239 research papers spanning 1975 to 2025."

    if any(w in question_stripped for w in ['thank', 'merci']):
        return "De rien ! N'hésitez pas à poser d'autres questions." if lang == "fr" else "You're welcome! Feel free to ask more."
    if any(w in question_stripped for w in ['bye', 'goodbye', 'au revoir', 'à bientôt']):
        return "Au revoir ! Revenez quand vous voulez." if lang == "fr" else "Goodbye! Come back anytime."

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if conversation_history:
        for turn in conversation_history[-8:]:
            messages.append(turn)

    is_conversational = False
    if conversation_history:
        ref_words = ['this','these','those','them','they','it','that','above','previous','mentioned','you said','you mentioned','the same','where do i find','where is','where are','where can','when was','when were','who wrote','which year','how many of','more about','tell me more','elaborate','go on','continue','what else','which ones','specifically','in detail','expand on','ceci','cela','ces','eux','ils','elle','ci-dessus','précédent','mentionné','plus de détails','expliquer','continuer']
        summary_words = ['summarize','summary','in short','briefly','define this','in a summary','short version','simplify','simpler terms','in a nutshell','quick summary','shorter','résumer','résumé','brièvement','en bref']
        if any(word in question_stripped for word in summary_words):
            is_conversational = True
        elif len(question.split()) <= 8 and any(word in question_stripped for word in ref_words):
            is_conversational = True

    if is_conversational:
        messages.append({"role": "user", "content": question})
        response = client.chat.completions.create(model="gpt-4o-mini", messages=messages, temperature=0.3, max_tokens=800)
        return response.choices[0].message.content

    corrected = correct_spelling(question) if len(question.split()) > 4 else question
    graph_context = query_graph(corrected)

    if graph_context == "NO_RESULTS":
        if conversation_history:
            messages.append({"role": "user", "content": question})
            response = client.chat.completions.create(model="gpt-4o-mini", messages=messages, temperature=0.3, max_tokens=800)
            answer = response.choices[0].message.content
            if "not available" not in answer.lower() and "don't have" not in answer.lower() and "pas disponible" not in answer.lower():
                return answer
        return NOT_AVAILABLE_FR if lang == "fr" else "This information is not available in the Devreotes Lab knowledge base."

    messages.append({"role": "user", "content": f"Based on the following knowledge graph data, answer the question.\n\nGRAPH CONTEXT:\n{graph_context}\n\nQUESTION: {question}"})
    response = client.chat.completions.create(model="gpt-4o-mini", messages=messages, temperature=0.3, max_tokens=800)
    return response.choices[0].message.content


# ============================================================
# PAPER UPLOAD
# ============================================================
EXTRACTION_PROMPT = """You are a biomedical research paper analyst for the Devreotes Lab at Johns Hopkins University.
Extract entities from this paper as JSON:
{"title":"Full paper title","authors":["Author 1"],"year":2025,"journal":"Journal name","proteins":[{"name":"Protein name","context":"Role (1 sentence)"}],"methods":[{"name":"Method name","context":"How used (1 sentence)"}],"concepts":[{"name":"Concept name","context":"Relation (1 sentence)"}],"model_organisms":[{"name":"Organism","context":"Why used (1 sentence)"}],"key_findings":"2-3 sentence summary","is_cell_biology":true}
Set is_cell_biology to true ONLY if related to cell biology, signal transduction, chemotaxis, cell migration. Return ONLY valid JSON."""


def extract_text_from_pdf(file_path):
    doc = fitz.open(file_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text.strip()


def extract_entities_from_text(text):
    truncated = text[:15000]
    if len(text) > 15000:
        truncated += "\n...\n" + text[-3000:]
    try:
        response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "system", "content": EXTRACTION_PROMPT}, {"role": "user", "content": truncated}], max_tokens=3000, temperature=0, response_format={"type": "json_object"})
        return json.loads(response.choices[0].message.content)
    except:
        return None


def escape_cypher(text):
    if not text:
        return ""
    return text.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')


def load_paper_to_graph(paper_num, entities):
    e = entities
    statements = []
    title = escape_cypher(e.get("title", f"Paper {paper_num}"))
    year = e.get("year", 0)
    journal = escape_cypher(e.get("journal", "Unknown"))
    key_findings = escape_cypher(e.get("key_findings", ""))
    authors = escape_cypher(", ".join(e.get("authors", [])))
    statements.append(f"MERGE (p:Paper {{number: '{paper_num}'}}) SET p.title = '{title}', p.year = {year}, p.journal = '{journal}', p.key_findings = '{key_findings}', p.authors = '{authors}';")
    for protein in e.get("proteins", []):
        n = escape_cypher(protein["name"])
        c = escape_cypher(protein.get("context", ""))
        statements.append(f"MERGE (pr:Protein {{name: '{n}'}}) WITH pr MATCH (p:Paper {{number: '{paper_num}'}}) MERGE (p)-[:MENTIONS_PROTEIN {{context: '{c}'}}]->(pr);")
        statements.append(f"MERGE (g:Gene {{name: '{n}'}}) WITH g MATCH (p:Paper {{number: '{paper_num}'}}) MERGE (p)-[:MENTIONS_GENE {{context: '{c}'}}]->(g);")
    for method in e.get("methods", []):
        n = escape_cypher(method["name"])
        c = escape_cypher(method.get("context", ""))
        statements.append(f"MERGE (m:Method {{name: '{n}'}}) WITH m MATCH (p:Paper {{number: '{paper_num}'}}) MERGE (p)-[:USES_METHOD {{context: '{c}'}}]->(m);")
    for concept in e.get("concepts", []):
        n = escape_cypher(concept["name"])
        c = escape_cypher(concept.get("context", ""))
        statements.append(f"MERGE (c:Concept {{name: '{n}'}}) WITH c MATCH (p:Paper {{number: '{paper_num}'}}) MERGE (p)-[:EXPLORES_CONCEPT {{context: '{c}'}}]->(c);")
    for organism in e.get("model_organisms", []):
        n = escape_cypher(organism["name"])
        c = escape_cypher(organism.get("context", ""))
        statements.append(f"MERGE (o:Organism {{name: '{n}'}}) WITH o MATCH (p:Paper {{number: '{paper_num}'}}) MERGE (p)-[:USES_ORGANISM {{context: '{c}'}}]->(o);")
    errors = []
    with driver.session() as session:
        for stmt in statements:
            try:
                session.run(stmt)
            except Exception as ex:
                errors.append(str(ex))
    return len(statements), errors


def get_next_paper_number():
    with driver.session() as session:
        result = session.run("MATCH (p:Paper) RETURN toInteger(p.number) AS num ORDER BY num DESC LIMIT 1")
        record = result.single()
        return str(record["num"] + 1) if record and record["num"] else "239"


def process_upload(file):
    if file is None:
        return "❌ No file uploaded.", ""
    try:
        text = extract_text_from_pdf(file.name)
    except Exception as ex:
        return f"❌ Failed to read PDF: {ex}", ""
    if len(text) < 200:
        return "❌ Could not extract enough text. May be a scanned document.", ""
    entities = extract_entities_from_text(text)
    if not entities:
        return "❌ Failed to extract entities.", ""
    relevance_note = ""
    if not entities.get("is_cell_biology", False):
        relevance_note = "\n\n⚠️ **Note:** This paper may not be directly related to the Devreotes Lab research areas.\n"
    title = entities.get("title", "Unknown")
    year = entities.get("year", "Unknown")
    proteins = [p["name"] for p in entities.get("proteins", [])]
    methods = [m["name"] for m in entities.get("methods", [])]
    concepts = [c["name"] for c in entities.get("concepts", [])]
    organisms = [o["name"] for o in entities.get("model_organisms", [])]
    findings = entities.get("key_findings", "")
    preview = f"📄 **{title}** ({year})\n\n**Proteins/Genes:** {', '.join(proteins) if proteins else 'None'}\n\n**Methods:** {', '.join(methods) if methods else 'None'}\n\n**Concepts:** {', '.join(concepts) if concepts else 'None'}\n\n**Organisms:** {', '.join(organisms) if organisms else 'None'}\n\n**Key findings:** {findings}{relevance_note}\n\n---\n*Enter password and click 'Add to Graph' to add this paper.*"
    return preview, json.dumps(entities)


def confirm_upload(entities_json, password):
    if password != UPLOAD_PASSWORD:
        return "❌ Incorrect password.", gr.update(visible=True)
    if not entities_json:
        return "❌ No paper to confirm.", gr.update(visible=True)
    try:
        entities = json.loads(entities_json)
    except:
        return "❌ Invalid data. Re-upload.", gr.update(visible=True)
    title = entities.get("title", "")
    with driver.session() as session:
        result = session.run("MATCH (p:Paper) WHERE toLower(p.title) CONTAINS $t RETURN p.number AS num, p.title AS title, p.year AS year LIMIT 3", t=title[:50].lower())
        duplicates = list(result)
        if duplicates:
            dup_list = "\n".join([f"  - Paper #{d['num']} ({d['year']}): {d['title']}" for d in duplicates])
            return f"⚠️ **Similar paper(s) already exist:**\n\n{dup_list}\n\nPaper NOT added.", gr.update(visible=True)
    paper_num = get_next_paper_number()
    loaded, errors = load_paper_to_graph(paper_num, entities)
    n_p = len(entities.get("proteins", []))
    n_m = len(entities.get("methods", []))
    n_c = len(entities.get("concepts", []))
    n_o = len(entities.get("model_organisms", []))
    if errors:
        return f"⚠️ Paper #{paper_num} added with {len(errors)} errors.", gr.update(visible=False)
    return f"✅ **Paper #{paper_num} added!**\n\n**Title:** {entities.get('title','')}\n\n**Added:** {n_p} proteins/genes, {n_m} methods, {n_c} concepts, {n_o} organisms\n\nNow queryable in the chatbot.", gr.update(visible=False)


def export_chat(chat_history):
    if not chat_history:
        return None
    lines = ["DEVREOTES LAB RESEARCH ASSISTANT — Chat Export\n" + "=" * 50 + "\n"]
    for msg in chat_history:
        if isinstance(msg, dict):
            lines.append(f"\n{msg.get('role', '').upper()}:\n{msg.get('content', '')}\n")
    path = "/tmp/chat_export.txt"
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path


def load_analytics():
    with driver.session() as session:
        pc = session.run("MATCH (p:Paper) RETURN count(p) AS c").single()["c"]
        prc = session.run("MATCH (n:Protein) RETURN count(n) AS c").single()["c"]
        gc = session.run("MATCH (n:Gene) RETURN count(n) AS c").single()["c"]
        mc = session.run("MATCH (n:Method) RETURN count(n) AS c").single()["c"]
        cc = session.run("MATCH (n:Concept) RETURN count(n) AS c").single()["c"]
        oc = session.run("MATCH (n:Organism) RETURN count(n) AS c").single()["c"]
        rc = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        decades = session.run("MATCH (p:Paper) WHERE p.year > 0 RETURN (p.year / 10) * 10 AS decade, count(p) AS count ORDER BY decade")
        decade_lines = [f"  {r['decade']}s: {'█' * (r['count'] // 2)} {r['count']}" for r in decades]
        top_p = session.run("MATCH (p:Paper)-[:MENTIONS_PROTEIN]->(pr:Protein) RETURN pr.name AS name, count(p) AS c ORDER BY c DESC LIMIT 5")
        p_lines = [f"  {r['name']}: {'█' * (r['c'] // 3)} {r['c']}" for r in top_p]
        top_m = session.run("MATCH (p:Paper)-[:USES_METHOD]->(m:Method) RETURN m.name AS name, count(p) AS c ORDER BY c DESC LIMIT 5")
        m_lines = [f"  {r['name']}: {'█' * (r['c'] // 2)} {r['c']}" for r in top_m]
        top_c = session.run("MATCH (p:Paper)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN c.name AS name, count(p) AS cnt ORDER BY cnt DESC LIMIT 5")
        c_lines = [f"  {r['name']}: {'█' * (r['cnt'] // 5)} {r['cnt']}" for r in top_c]
    return f"**Overview**\n- Papers: **{pc}** | Proteins: **{prc}** | Genes: **{gc}**\n- Methods: **{mc}** | Concepts: **{cc}** | Organisms: **{oc}**\n- Relationships: **{rc}**\n\n**Papers by Decade**\n```\n" + "\n".join(decade_lines) + "\n```\n\n**Top Proteins/Genes**\n```\n" + "\n".join(p_lines) + "\n```\n\n**Top Methods**\n```\n" + "\n".join(m_lines) + "\n```\n\n**Top Concepts**\n```\n" + "\n".join(c_lines) + "\n```"


def lookup_paper(paper_num_str):
    if not paper_num_str:
        return "Select a paper number to view details."
    pnum = re.sub(r'[^0-9]', '', str(paper_num_str))
    if not pnum:
        return "Enter a valid paper number."
    with driver.session() as session:
        result = session.run("MATCH (p:Paper {number: $num}) OPTIONAL MATCH (p)-[r1:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[r2:USES_METHOD]->(m:Method) OPTIONAL MATCH (p)-[r3:EXPLORES_CONCEPT]->(c:Concept) OPTIONAL MATCH (p)-[r4:USES_ORGANISM]->(o:Organism) RETURN p.title AS title, p.year AS year, p.journal AS journal, p.authors AS authors, p.key_findings AS findings, collect(DISTINCT pr.name) AS proteins, collect(DISTINCT m.name) AS methods, collect(DISTINCT c.name) AS concepts, collect(DISTINCT o.name) AS organisms", num=pnum)
        record = result.single()
        if not record:
            return f"Paper #{pnum} not found in the knowledge base."
        title = record['title'] or 'Unknown'
        search_url = f"https://scholar.google.com/scholar?q={'+'.join(title.split()[:10])}"
        return (f"**Paper #{pnum}** ({record['year']})\n\n**Title:** {title}\n\n**Journal:** {record['journal'] or 'N/A'}\n\n**Authors:** {record['authors'] or 'N/A'}\n\n**Key Findings:** {record['findings'] or 'N/A'}\n\n**Proteins/Genes:** {', '.join(record['proteins']) if record['proteins'] else 'None'}\n\n**Methods:** {', '.join(record['methods']) if record['methods'] else 'None'}\n\n**Concepts:** {', '.join(record['concepts']) if record['concepts'] else 'None'}\n\n**Organisms:** {', '.join(record['organisms']) if record['organisms'] else 'None'}\n\n[🔗 Find on Google Scholar]({search_url})")


search_history = {}
history_order = []


def chat_fn(message, history):
    global search_history, history_order
    conversation_history = []
    for h in history:
        if isinstance(h, dict):
            conversation_history.append(h)
    lang = detect_language(message)
    answer = generate_answer(message, conversation_history, lang)
    skip = ['hi', 'hello', 'hey', 'thanks', 'thank you', 'bye', 'goodbye', 'bonjour', 'salut', 'merci', 'au revoir', '']
    if message.strip().lower() not in skip:
        search_history[message] = answer
        if message in history_order:
            history_order.remove(message)
        history_order.insert(0, message)
        history_order[:] = history_order[:20]
    return answer


def get_visible_history():
    return history_order[:10]


# ============================================================
# UI
# ============================================================
with gr.Blocks(title="Devreotes Lab Research Assistant") as demo:

    upload_entities_state = gr.State("")

    gr.HTML("""
        <div style="background: linear-gradient(135deg, #0a1628 0%, #1a3050 40%, #0f2440 70%, #0a1628 100%);
                    padding: 24px 32px; border-bottom: 2px solid rgba(79,172,254,0.2);
                    display: flex; align-items: center; justify-content: center; gap: 20px;
                    position: relative; overflow: hidden;">
            <div style="position:absolute; top:0; left:0; right:0; bottom:0;
                        background: radial-gradient(ellipse at 20% 50%, rgba(79,172,254,0.08) 0%, transparent 60%),
                                    radial-gradient(ellipse at 80% 50%, rgba(120,200,255,0.05) 0%, transparent 50%);
                        pointer-events: none;"></div>
            <div style="position: relative;">
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
            <div style="position: relative; text-align: center;">
                <div style="font-size: 22px; font-weight: 700; color: #e8f0fe; letter-spacing: -0.5px; font-family: 'Georgia', serif;">
                    Devreotes Lab Research Assistant</div>
                <div style="font-size: 12px; color: #4facfe; font-weight: 500; letter-spacing: 1px; margin-top: 2px; text-transform: uppercase;">
                    GraphRAG &middot; 239 Papers &middot; Johns Hopkins University School of Medicine</div>
                <div style="font-size: 10px; color: #5a7a9a; margin-top: 4px;">
                    🌐 English &amp; Fran&ccedil;ais &middot; 🎤 Voice Input Supported</div>
            </div>
        </div>
    """)

    with gr.Row():
        # LEFT SIDEBAR
        with gr.Column(scale=1, min_width=220):
            gr.HTML("""<div style="padding: 12px;">
                <div style="color: #4facfe; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 12px;">Knowledge Graph</div>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px;">
                    <div style="background: #111d30; border: 1px solid rgba(79,172,254,0.12); border-radius: 10px; padding: 12px; text-align: center;"><div style="font-size: 22px; font-weight: 700; color: #4facfe;">239</div><div style="font-size: 9px; color: #5a6d85; text-transform: uppercase;">Papers</div></div>
                    <div style="background: #111d30; border: 1px solid rgba(79,172,254,0.12); border-radius: 10px; padding: 12px; text-align: center;"><div style="font-size: 22px; font-weight: 700; color: #4facfe;">738</div><div style="font-size: 9px; color: #5a6d85; text-transform: uppercase;">Proteins</div></div>
                    <div style="background: #111d30; border: 1px solid rgba(79,172,254,0.12); border-radius: 10px; padding: 12px; text-align: center;"><div style="font-size: 22px; font-weight: 700; color: #4facfe;">531</div><div style="font-size: 9px; color: #5a6d85; text-transform: uppercase;">Methods</div></div>
                    <div style="background: #111d30; border: 1px solid rgba(79,172,254,0.12); border-radius: 10px; padding: 12px; text-align: center;"><div style="font-size: 22px; font-weight: 700; color: #4facfe;">330</div><div style="font-size: 9px; color: #5a6d85; text-transform: uppercase;">Concepts</div></div>
                </div>
                <div style="color: #4facfe; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.5px; margin-top: 16px; margin-bottom: 8px;">Recent Searches</div>
            </div>""")
            history_dropdown = gr.Dropdown(choices=[], label="Click to replay", interactive=True, allow_custom_value=False)
            gr.HTML("""<div style="padding: 0 12px;"><div style="color: #4facfe; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.5px; margin-top: 16px; margin-bottom: 8px;">📊 Analytics</div></div>""")
            analytics_btn = gr.Button("Load Analytics", size="sm", variant="secondary")
            analytics_display = gr.Markdown("")

        # CENTER CHAT
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(height=480, show_label=False)
            with gr.Row():
                msg = gr.Textbox(placeholder="Type or use voice 🎤 (English / Français)...", show_label=False, scale=4)
                voice_input = gr.Audio(sources=["microphone"], type="filepath", label="🎤", scale=1)
                send_btn = gr.Button("Send", scale=1, variant="primary")
            with gr.Row():
                export_btn = gr.Button("📥 Export", size="sm", variant="secondary")
                add_paper_btn = gr.Button("📤 Add Paper", size="sm", variant="secondary")
            export_file = gr.File(label="Download Chat", visible=False)
            with gr.Group(visible=False) as upload_section:
                gr.HTML('<div style="color: #4facfe; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 8px;">Upload New Paper</div>')
                with gr.Row():
                    upload_file = gr.File(label="Select PDF", file_types=[".pdf"], scale=3)
                    upload_btn = gr.Button("🔍 Analyze", size="sm", variant="secondary", scale=1)
                upload_preview = gr.Markdown("")
                with gr.Row():
                    upload_password = gr.Textbox(placeholder="Password to confirm", type="password", show_label=False, scale=2)
                    confirm_btn = gr.Button("✅ Add to Graph", size="sm", variant="primary", visible=False, scale=1)
                upload_result = gr.Markdown("")
                close_upload_btn = gr.Button("✕ Close", size="sm", variant="secondary")

        # RIGHT SIDEBAR
        with gr.Column(scale=1, min_width=220):
            gr.HTML("""<div style="padding: 12px;">
                <div style="color: #4facfe; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 12px;">About the Lab</div>
                <div style="background: #111d30; border: 1px solid rgba(79,172,254,0.12); border-radius: 12px; padding: 14px; font-size: 12px; color: #8a9bb8; line-height: 1.6;">
                    <strong style="color: #d0ddf0;">Prof. Peter N. Devreotes</strong><br>Department of Cell Biology<br>Johns Hopkins School of Medicine<br><br>
                    Research spanning <strong style="color: #8ab4f8;">1975 &ndash; 2025</strong> focusing on cell migration, chemotaxis, signal transduction, and the cytoskeleton.</div>
                <div style="color: #4facfe; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.5px; margin-top: 16px; margin-bottom: 8px;">🔎 Paper Finder</div>
            </div>""")
            paper_input = gr.Textbox(placeholder="Enter paper # (e.g., 276)", show_label=False)
            paper_lookup_btn = gr.Button("Look Up Paper", size="sm", variant="secondary")
            paper_details = gr.Markdown("Enter a paper number to view its details.")
            gr.HTML("""<div style="padding: 0 12px;"><div style="color: #4facfe; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.5px; margin-top: 16px; margin-bottom: 8px;">💡 Try Asking</div></div>""")
            s1 = gr.Button("How does Ras regulate cell migration?", size="sm")
            s2 = gr.Button("Which papers use optogenetics?", size="sm")
            s3 = gr.Button("What is the role of PTEN in chemotaxis?", size="sm")
            s4 = gr.Button("Quel est le rôle de la chimiotaxie?", size="sm")
            s5 = gr.Button("What methods study cell polarity?", size="sm")
            s6 = gr.Button("Papers from the 2020s", size="sm")

    # EVENT HANDLERS
    def respond(message, chat_history):
        if not message or not message.strip():
            return "", chat_history, gr.update(choices=get_visible_history(), value=None)
        bot_response = chat_fn(message, chat_history)
        chat_history.append({"role": "user", "content": message})
        chat_history.append({"role": "assistant", "content": bot_response})
        return "", chat_history, gr.update(choices=get_visible_history(), value=None)

    def respond_voice(audio_path, chat_history):
        if not audio_path:
            return "", chat_history, gr.update(choices=get_visible_history(), value=None), None
        text, lang = transcribe_audio(audio_path)
        if not text or text.startswith("[Voice"):
            chat_history.append({"role": "user", "content": "🎤 (voice input)"})
            chat_history.append({"role": "assistant", "content": "Sorry, I couldn't understand the audio. Please try again or type your question."})
            return "", chat_history, gr.update(choices=get_visible_history(), value=None), None
        bot_response = chat_fn(text, chat_history)
        chat_history.append({"role": "user", "content": f"🎤 {text}"})
        chat_history.append({"role": "assistant", "content": bot_response})
        return "", chat_history, gr.update(choices=get_visible_history(), value=None), None

    msg.submit(respond, [msg, chatbot], [msg, chatbot, history_dropdown])
    send_btn.click(respond, [msg, chatbot], [msg, chatbot, history_dropdown])
    voice_input.stop_recording(respond_voice, [voice_input, chatbot], [msg, chatbot, history_dropdown, voice_input])

    for btn in [s1, s2, s3, s4, s5, s6]:
        btn.click(fn=lambda suggestion, h: ("", h + [{"role": "user", "content": suggestion}, {"role": "assistant", "content": chat_fn(suggestion, h)}], gr.update(choices=get_visible_history(), value=None)), inputs=[btn, chatbot], outputs=[msg, chatbot, history_dropdown])

    def replay_history(selected, chat_history):
        if selected and selected in search_history:
            chat_history.append({"role": "user", "content": selected})
            chat_history.append({"role": "assistant", "content": search_history[selected]})
        return chat_history, gr.update(value=None)

    history_dropdown.change(replay_history, [history_dropdown, chatbot], [chatbot, history_dropdown])

    add_paper_btn.click(fn=lambda: gr.update(visible=True), outputs=[upload_section])
    close_upload_btn.click(fn=lambda: gr.update(visible=False), outputs=[upload_section])

    def handle_upload(file):
        preview, entities_json = process_upload(file)
        if entities_json:
            return preview, entities_json, gr.update(visible=True)
        return preview, "", gr.update(visible=False)

    upload_btn.click(handle_upload, [upload_file], [upload_preview, upload_entities_state, confirm_btn])
    confirm_btn.click(confirm_upload, [upload_entities_state, upload_password], [upload_result, confirm_btn])

    def handle_export(chat_history):
        path = export_chat(chat_history)
        if path:
            return gr.update(value=path, visible=True)
        return gr.update(visible=False)

    export_btn.click(handle_export, [chatbot], [export_file])
    analytics_btn.click(load_analytics, outputs=[analytics_display])
    paper_lookup_btn.click(lookup_paper, [paper_input], [paper_details])

demo.launch(server_name="0.0.0.0", server_port=7860)