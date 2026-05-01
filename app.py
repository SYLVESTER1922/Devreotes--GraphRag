import os
import re
import json
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

def transcribe_audio(audio_path):
    if not audio_path: return "", "en"
    try:
        with open(audio_path, "rb") as f:
            response = client.audio.transcriptions.create(model="whisper-1", file=f, response_format="verbose_json")
        return response.text.strip(), getattr(response, 'language', 'en') or 'en'
    except: return "", "en"

def correct_spelling(question):
    try:
        response = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "system", "content": "You are a spelling corrector for biology research questions in English or French. Fix any spelling errors and return ONLY the corrected question. If no errors, return unchanged."}, {"role": "user", "content": question}], temperature=0, max_tokens=150)
        return response.choices[0].message.content.strip()
    except: return question

def extract_keywords(question):
    stop_words = {'what','which','where','when','how','does','about','tell','show','find','papers','paper','that','this','with','from','have','been','their','they','there','were','using','used','use','the','and','for','are','can','you','me','is','it','in','of','to','do','a','an','on','by','has','was','be','or','at','its','who','did','will','would','could','should','between','through','role','most','many','some','any','all','more','than','these','those','into','also','but','not','only','such','other','each','every','much','study','studies','research','describe','explain','discuss','related','involved','connection','connected','relationship','know','known','like','make','makes','give','list','name','please','want','need','think','things','compare','comparison','mentioned','mention','le','la','les','de','du','des','un','une','et','est','en','que','qui','dans','pour','sur','avec','ce','cette','sont','par','au','aux','ou','mais','pas','ne','ont','fait','plus','quels','quelle','quelles','quel','comment','quoi','pourquoi'}
    words = re.findall(r'[a-zA-Z0-9/()-]+', question)
    return [w for w in words if w.lower() not in stop_words and len(w) > 2]

def query_graph(question):
    keywords = extract_keywords(question)
    ql = question.lower()
    ctx = []
    seen = set()
    with driver.session() as s:
        pnums = re.findall(r'#(\d{1,3})\b', question)
        if not pnums: pnums = re.findall(r'(?:paper|papier|article)\s+(\d{1,3})\b', ql)
        if pnums:
            for pn in pnums[:10]:
                for r in s.run("MATCH (p:Paper {number:$n}) OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) OPTIONAL MATCH (p)-[:USES_ORGANISM]->(o:Organism) RETURN p.number AS pn,p.title AS t,p.year AS y,p.key_findings AS f,p.journal AS j,p.authors AS a,collect(DISTINCT pr.name) AS pr,collect(DISTINCT m.name) AS me,collect(DISTINCT c.name) AS co,collect(DISTINCT o.name) AS og", n=pn):
                    if r['pn'] not in seen:
                        ctx.append(f"PAPER #{r['pn']} ({r['y']}): '{r['t']}'\n    Journal: {r['j']}\n    Authors: {r['a']}\n    Findings: {r['f']}\n    Proteins: {r['pr']}\n    Methods: {r['me']}\n    Concepts: {r['co']}\n    Organisms: {r['og']}")
                        seen.add(r['pn'])
        if any(w in ql for w in ['latest','newest','recent','most recent','plus récent','dernier','dernière']):
            for r in s.run("MATCH (p:Paper) WHERE p.year IS NOT NULL OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS pn,p.title AS t,p.year AS y,p.key_findings AS f,collect(DISTINCT pr.name) AS pr,collect(DISTINCT m.name) AS me,collect(DISTINCT c.name) AS co ORDER BY p.year DESC LIMIT 5"):
                ctx.append(f"  Paper #{r['pn']} ({r['y']}): '{r['t']}'\n    Findings: {r['f']}\n    Proteins: {r['pr']}\n    Methods: {r['me']}\n    Concepts: {r['co']}")
        if any(w in ql for w in ['oldest','earliest','first paper','premier','ancien','plus ancien']):
            for r in s.run("MATCH (p:Paper) WHERE p.year IS NOT NULL AND p.year>0 OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS pn,p.title AS t,p.year AS y,p.key_findings AS f,collect(DISTINCT pr.name) AS pr,collect(DISTINCT c.name) AS co ORDER BY p.year ASC LIMIT 5"):
                ctx.append(f"  Paper #{r['pn']} ({r['y']}): '{r['t']}'\n    Findings: {r['f']}\n    Proteins: {r['pr']}\n    Concepts: {r['co']}")
        dm = re.search(r'\b(19[7-9]0|20[0-2]0)s?\b', ql)
        ym = re.search(r'\b(19[7-9]\d|20[0-2]\d)\b', question)
        if dm:
            dec = int(dm.group(1)[:4])
            recs = list(s.run("MATCH (p:Paper) WHERE p.year>=$s AND p.year<$e OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS pn,p.title AS t,p.year AS y,collect(DISTINCT c.name) AS co ORDER BY p.year LIMIT 15", s=dec, e=dec+10))
            if recs:
                ctx.append(f"PAPERS FROM THE {dec}s ({len(recs)} found):")
                for r in recs:
                    if r['pn'] not in seen: ctx.append(f"  Paper #{r['pn']} ({r['y']}): '{r['t']}' — {r['co']}"); seen.add(r['pn'])
        elif ym and not dm:
            yr = int(ym.group(1))
            recs = list(s.run("MATCH (p:Paper {year:$y}) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS pn,p.title AS t,p.year AS y,collect(DISTINCT c.name) AS co ORDER BY p.year LIMIT 15", y=yr))
            if recs:
                ctx.append(f"PAPERS FROM {yr} ({len(recs)} found):")
                for r in recs:
                    if r['pn'] not in seen: ctx.append(f"  Paper #{r['pn']} ({r['y']}): '{r['t']}' — {r['co']}"); seen.add(r['pn'])
        if any(p in ql for p in ['list all','show all','all proteins','all methods','all concepts','all organisms','all genes','top proteins','top methods','top concepts','top genes','most studied','most used','most common','frequently','toutes les','tous les','plus étudié']):
            if any(w in ql for w in ['protein','gene','protéine','gène']):
                recs = list(s.run("MATCH (p:Paper)-[:MENTIONS_PROTEIN]->(pr:Protein) RETURN pr.name AS name,count(p) AS c ORDER BY c DESC LIMIT 20"))
                if recs: ctx.append("TOP PROTEINS/GENES:"); [ctx.append(f"  {r['name']}: {r['c']} papers") for r in recs]
            elif any(w in ql for w in ['method','technique','méthode']):
                recs = list(s.run("MATCH (p:Paper)-[:USES_METHOD]->(m:Method) RETURN m.name AS name,count(p) AS c ORDER BY c DESC LIMIT 20"))
                if recs: ctx.append("TOP METHODS:"); [ctx.append(f"  {r['name']}: {r['c']} papers") for r in recs]
            elif any(w in ql for w in ['concept','topic','theme','sujet']):
                recs = list(s.run("MATCH (p:Paper)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN c.name AS name,count(p) AS c ORDER BY c DESC LIMIT 20"))
                if recs: ctx.append("TOP CONCEPTS:"); [ctx.append(f"  {r['name']}: {r['c']} papers") for r in recs]
            elif any(w in ql for w in ['organism','species','organisme','espèce']):
                recs = list(s.run("MATCH (p:Paper)-[:USES_ORGANISM]->(o:Organism) RETURN o.name AS name,count(p) AS c ORDER BY c DESC LIMIT 20"))
                if recs: ctx.append("TOP ORGANISMS:"); [ctx.append(f"  {r['name']}: {r['c']} papers") for r in recs]
            else:
                for lb,rl,nd in [("PROTEINS","MENTIONS_PROTEIN","Protein"),("METHODS","USES_METHOD","Method"),("CONCEPTS","EXPLORES_CONCEPT","Concept")]:
                    recs = list(s.run(f"MATCH (p:Paper)-[:{rl}]->(n:{nd}) RETURN n.name AS name,count(p) AS c ORDER BY c DESC LIMIT 10"))
                    if recs: ctx.append(f"TOP {lb}:"); [ctx.append(f"  {r['name']}: {r['c']} papers") for r in recs]
        if any(p in ql for p in ['connected to','connection between','related to','relationship between','link between','lien entre','connexion entre','relié à']):
            if len(keywords) >= 2:
                k1,k2 = keywords[0].lower(),keywords[1].lower()
                recs = list(s.run("MATCH (p:Paper)-[]->(e1) WHERE toLower(e1.name) CONTAINS $k1 WITH p MATCH (p)-[]->(e2) WHERE toLower(e2.name) CONTAINS $k2 OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS pn,p.title AS t,p.year AS y,p.key_findings AS f,collect(DISTINCT pr.name) AS pr,collect(DISTINCT c.name) AS co LIMIT 10", k1=k1, k2=k2))
                if recs:
                    ctx.append(f"PAPERS CONNECTING '{keywords[0]}' AND '{keywords[1]}':")
                    for r in recs:
                        if r['pn'] not in seen: ctx.append(f"  Paper #{r['pn']} ({r['y']}): '{r['t']}'\n    Findings: {r['f']}\n    Proteins: {r['pr']}\n    Concepts: {r['co']}"); seen.add(r['pn'])
        for kw in keywords:
            kl = kw.lower(); terms = [kl]
            if len(kl) >= 5: terms.extend([kl[1:], kl[:-1]])
            for term in terms:
                found = False
                for rl,nd,lb in [("MENTIONS_PROTEIN","Protein","protein"),("MENTIONS_GENE","Gene","gene"),("USES_METHOD","Method","method"),("EXPLORES_CONCEPT","Concept","concept"),("USES_ORGANISM","Organism","organism")]:
                    recs = list(s.run(f"MATCH (p:Paper)-[r:{rl}]->(n:{nd}) WHERE toLower(n.name) CONTAINS $q RETURN p.number AS pn,p.title AS t,p.year AS y,n.name AS e,r.context AS c ORDER BY p.year DESC LIMIT 10", q=term))
                    if recs:
                        ctx.append(f"\n{lb.upper()} MATCHES for '{kw}':")
                        for r in recs:
                            if r['pn'] not in seen: ctx.append(f"  Paper #{r['pn']} ({r['y']}): '{r['t']}' — {r['e']}: {r['c']}"); seen.add(r['pn'])
                        found = True; break
                if found: break
        for kw in keywords:
            recs = list(s.run("MATCH (p:Paper) WHERE toLower(p.title) CONTAINS $q OR toLower(p.key_findings) CONTAINS $q OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS pn,p.title AS t,p.year AS y,p.key_findings AS f,collect(DISTINCT pr.name) AS pr,collect(DISTINCT m.name) AS me,collect(DISTINCT c.name) AS co LIMIT 5", q=kw.lower()))
            nr = [r for r in recs if r['pn'] not in seen]
            if nr:
                ctx.append(f"\nPAPER MATCHES for '{kw}':")
                for r in nr: ctx.append(f"  Paper #{r['pn']} ({r['y']}): '{r['t']}'\n    Findings: {r['f']}\n    Proteins: {r['pr']}\n    Methods: {r['me']}"); seen.add(r['pn'])
        if any(w in ql for w in ['how many','total','overview','statistics','stat','combien','statistiques','aperçu']):
            counts = {}
            for lb in ['Paper','Protein','Gene','Method','Concept','Organism']:
                counts[lb] = s.run(f"MATCH (n:{lb}) RETURN count(n) AS c").single()["c"]
            counts['Rel'] = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            ctx.append(f"\nGRAPH STATISTICS:\n  Papers: {counts['Paper']}\n  Proteins: {counts['Protein']}\n  Genes: {counts['Gene']}\n  Methods: {counts['Method']}\n  Concepts: {counts['Concept']}\n  Organisms: {counts['Organism']}\n  Relationships: {counts['Rel']}")
    return "\n".join(ctx) if ctx else "NO_RESULTS"

def get_system_prompt(lang):
    base = """You are the Devreotes Lab Research Assistant for Professor Peter Devreotes' lab at Johns Hopkins University School of Medicine.
The knowledge graph contains 238 papers (1975-2025), 738 proteins/genes, 531 methods, 330 concepts, and 228 organisms.
RULES:
1. Answer from graph context only. Synthesize naturally. 2. Concise: 2-4 sentences for simple questions. 3. Always cite paper numbers. 4. If not in data, say information is not available. 5. ALWAYS cite papers, never generic definitions. 6. Never suggest outside knowledge. 7. Brief paper descriptions. 8. Highlight cross-paper connections. 9. Use conversation history for follow-ups. 10. Summarize concisely when asked.
11. After your main answer, add TWO blank lines, then suggest exactly 3 follow-up questions prefixed with "💡". Keep them brief, relevant to your answer, and useful for deeper exploration."""
    if lang == "fr":
        base += "\n12. RESPOND ENTIRELY IN FRENCH. Use 'Article #' instead of 'Paper #'. Follow-up suggestions in French too."
    return base

GREETING_EN = "Hello! I'm the Devreotes Lab Research Assistant — ask me anything about Professor Devreotes' 238 published papers on cell migration, chemotaxis, and signal transduction.\n\n💡 How does Ras regulate cell migration?\n💡 What is the most studied protein?\n💡 Which papers use optogenetics?"
GREETING_FR = "Bonjour ! Je suis l'assistant de recherche du laboratoire Devreotes — posez-moi n'importe quelle question sur les 238 articles publiés.\n\n💡 Comment Ras régule-t-il la migration cellulaire ?\n💡 Quelle est la protéine la plus étudiée ?\n💡 Quels articles utilisent l'optogénétique ?"
GREET_EN = {'hi','hello','hey','good morning','good afternoon','good evening','howdy','greetings','sup','yo',"what's up",'whats up'}
GREET_FR = {'bonjour','salut','bonsoir','coucou'}

def generate_answer(question, conv_hist=None, lang="en"):
    if not question or not question.strip():
        return "Veuillez taper une question." if lang=="fr" else "Please type a question."
    q = question.strip().lower().rstrip('!?.,:;')
    if q in GREET_EN or any(q.startswith(g) for g in GREET_EN): return GREETING_FR if lang=="fr" else GREETING_EN
    if q in GREET_FR or any(q.startswith(g) for g in GREET_FR): return GREETING_FR
    if any(t in q for t in ['how are you','who are you','what are you','your name','what can you do','help me','comment allez','qui êtes','aide']):
        if lang=="fr": return "Je suis l'assistant du laboratoire Devreotes. Je réponds à vos questions sur les protéines, gènes, méthodes et concepts issus de 238 articles (1975-2025).\n\n💡 Qu'est-ce que PTEN ?\n💡 Articles des années 2020 ?\n💡 Combien de protéines dans la base ?"
        return "I'm the Devreotes Lab Research Assistant. I answer questions about proteins, genes, methods, concepts, and organisms from 238 papers (1975-2025).\n\n💡 What is PTEN?\n💡 Papers from the 2020s?\n💡 How many proteins in the database?"
    if any(w in q for w in ['thank','merci']): return "De rien !" if lang=="fr" else "You're welcome!"
    if any(w in q for w in ['bye','goodbye','au revoir']): return "Au revoir !" if lang=="fr" else "Goodbye!"

    msgs = [{"role":"system","content":get_system_prompt(lang)}]
    if conv_hist:
        for t in conv_hist[-8:]: msgs.append(t)
    is_conv = False
    if conv_hist:
        ref = ['this','these','those','them','they','it','that','above','previous','mentioned','you said','the same','where is','where are','where can','when was','when were','who wrote','which year','more about','tell me more','elaborate','go on','continue','what else','which ones','specifically','expand on','ceci','cela','ces','eux','ils','ci-dessus','précédent','mentionné','plus de détails','expliquer','continuer']
        summ = ['summarize','summary','in short','briefly','short version','simplify','in a nutshell','shorter','résumer','résumé','brièvement','en bref']
        if any(w in q for w in summ): is_conv = True
        elif len(question.split()) <= 8 and any(w in q for w in ref): is_conv = True
    if is_conv:
        msgs.append({"role":"user","content":question})
        return client.chat.completions.create(model="gpt-4o-mini", messages=msgs, temperature=0.3, max_tokens=800).choices[0].message.content

    corrected = correct_spelling(question) if len(question.split()) > 4 else question
    gc = query_graph(corrected)
    if gc == "NO_RESULTS":
        if conv_hist:
            msgs.append({"role":"user","content":question})
            ans = client.chat.completions.create(model="gpt-4o-mini", messages=msgs, temperature=0.3, max_tokens=800).choices[0].message.content
            if not any(w in ans.lower() for w in ['not available','pas disponible']): return ans
        return "Cette information n'est pas disponible dans la base de connaissances." if lang=="fr" else "This information is not available in the Devreotes Lab knowledge base."
    msgs.append({"role":"user","content":f"Based on the following knowledge graph data, answer the question.\n\nGRAPH CONTEXT:\n{gc}\n\nQUESTION: {question}"})
    return client.chat.completions.create(model="gpt-4o-mini", messages=msgs, temperature=0.3, max_tokens=800).choices[0].message.content

# PAPER UPLOAD
EXTRACTION_PROMPT = """Extract entities from this biomedical paper as JSON:
{"title":"","authors":[],"year":0,"journal":"","proteins":[{"name":"","context":""}],"methods":[{"name":"","context":""}],"concepts":[{"name":"","context":""}],"model_organisms":[{"name":"","context":""}],"key_findings":"","is_cell_biology":true}
Set is_cell_biology true ONLY if related to cell biology/chemotaxis/signal transduction. Return ONLY valid JSON."""

def extract_text_from_pdf(fp):
    d=fitz.open(fp); t="".join(p.get_text() for p in d); d.close(); return t.strip()

def extract_entities_from_text(text):
    t=text[:15000]+("\n...\n"+text[-3000:] if len(text)>15000 else "")
    try: return json.loads(client.chat.completions.create(model="gpt-4o-mini",messages=[{"role":"system","content":EXTRACTION_PROMPT},{"role":"user","content":t}],max_tokens=3000,temperature=0,response_format={"type":"json_object"}).choices[0].message.content)
    except: return None

def esc(t): return t.replace("\\","\\\\").replace("'","\\'").replace('"','\\"') if t else ""

def load_paper_to_graph(pn, e):
    st=[]
    st.append(f"MERGE (p:Paper {{number:'{pn}'}}) SET p.title='{esc(e.get('title',''))}',p.year={e.get('year',0)},p.journal='{esc(e.get('journal',''))}',p.key_findings='{esc(e.get('key_findings',''))}',p.authors='{esc(', '.join(e.get('authors',[])))}'")
    for x in e.get("proteins",[]):
        n=esc(x["name"]); c=esc(x.get("context",""))
        st.append(f"MERGE (pr:Protein {{name:'{n}'}}) WITH pr MATCH (p:Paper {{number:'{pn}'}}) MERGE (p)-[:MENTIONS_PROTEIN {{context:'{c}'}}]->(pr)")
        st.append(f"MERGE (g:Gene {{name:'{n}'}}) WITH g MATCH (p:Paper {{number:'{pn}'}}) MERGE (p)-[:MENTIONS_GENE {{context:'{c}'}}]->(g)")
    for x in e.get("methods",[]): n=esc(x["name"]); c=esc(x.get("context","")); st.append(f"MERGE (m:Method {{name:'{n}'}}) WITH m MATCH (p:Paper {{number:'{pn}'}}) MERGE (p)-[:USES_METHOD {{context:'{c}'}}]->(m)")
    for x in e.get("concepts",[]): n=esc(x["name"]); c=esc(x.get("context","")); st.append(f"MERGE (c:Concept {{name:'{n}'}}) WITH c MATCH (p:Paper {{number:'{pn}'}}) MERGE (p)-[:EXPLORES_CONCEPT {{context:'{c}'}}]->(c)")
    for x in e.get("model_organisms",[]): n=esc(x["name"]); c=esc(x.get("context","")); st.append(f"MERGE (o:Organism {{name:'{n}'}}) WITH o MATCH (p:Paper {{number:'{pn}'}}) MERGE (p)-[:USES_ORGANISM {{context:'{c}'}}]->(o)")
    errs=[]
    with driver.session() as ss:
        for s2 in st:
            try: ss.run(s2)
            except Exception as ex: errs.append(str(ex))
    return len(st),errs

def get_next_paper_number():
    with driver.session() as s:
        r=s.run("MATCH (p:Paper) RETURN toInteger(p.number) AS n ORDER BY n DESC LIMIT 1").single()
        return str(r["n"]+1) if r and r["n"] else "239"

def process_upload(file):
    if not file: return "❌ No file uploaded.",""
    try: text=extract_text_from_pdf(file.name)
    except Exception as ex: return f"❌ Failed: {ex}",""
    if len(text)<200: return "❌ Not enough text.",""
    ent=extract_entities_from_text(text)
    if not ent: return "❌ Extraction failed.",""
    rn="" if ent.get("is_cell_biology",False) else "\n\n⚠️ May not be related to Devreotes Lab.\n"
    pr=", ".join(p["name"] for p in ent.get("proteins",[])) or "None"
    me=", ".join(m["name"] for m in ent.get("methods",[])) or "None"
    co=", ".join(c["name"] for c in ent.get("concepts",[])) or "None"
    og=", ".join(o["name"] for o in ent.get("model_organisms",[])) or "None"
    return f"📄 **{ent.get('title','?')}** ({ent.get('year','?')})\n\n**Proteins/Genes:** {pr}\n**Methods:** {me}\n**Concepts:** {co}\n**Organisms:** {og}\n**Findings:** {ent.get('key_findings','')}{rn}\n\n---\n*Enter password to add.*", json.dumps(ent)

def confirm_upload(ej,pw):
    if pw!=UPLOAD_PASSWORD: return "❌ Wrong password.",gr.update(visible=True)
    if not ej: return "❌ No paper.",gr.update(visible=True)
    try: ent=json.loads(ej)
    except: return "❌ Invalid.",gr.update(visible=True)
    ti=ent.get("title","")
    with driver.session() as ss:
        dups=list(ss.run("MATCH (p:Paper) WHERE toLower(p.title) CONTAINS $t RETURN p.number AS n,p.title AS t,p.year AS y LIMIT 3",t=ti[:50].lower()))
        if dups: return f"⚠️ Similar exists:\n\n"+"\n".join([f"  - #{d['n']} ({d['y']}): {d['t']}" for d in dups])+"\n\nNOT added.",gr.update(visible=True)
    pn=get_next_paper_number(); _,errs=load_paper_to_graph(pn,ent)
    np=len(ent.get("proteins",[])); nm=len(ent.get("methods",[])); nc=len(ent.get("concepts",[])); no=len(ent.get("model_organisms",[]))
    return f"✅ **Paper #{pn} added!**\n{ent.get('title','')}\n{np} proteins, {nm} methods, {nc} concepts, {no} organisms",gr.update(visible=False)

def export_chat(ch):
    if not ch: return None
    lines=["DEVREOTES LAB — Chat Export\n"+"="*50]
    for m in ch:
        if isinstance(m,dict): lines.append(f"\n{m.get('role','').upper()}:\n{m.get('content','')}")
    p="/tmp/chat_export.txt"
    with open(p,"w") as f: f.write("\n".join(lines))
    return p

def load_analytics():
    with driver.session() as s:
        pc=s.run("MATCH (p:Paper) RETURN count(p) AS c").single()["c"]
        prc=s.run("MATCH (n:Protein) RETURN count(n) AS c").single()["c"]
        gc=s.run("MATCH (n:Gene) RETURN count(n) AS c").single()["c"]
        mc=s.run("MATCH (n:Method) RETURN count(n) AS c").single()["c"]
        cc=s.run("MATCH (n:Concept) RETURN count(n) AS c").single()["c"]
        oc=s.run("MATCH (n:Organism) RETURN count(n) AS c").single()["c"]
        rc=s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        dl=[f"  {r['d']}s: {'█'*(r['c']//2)} {r['c']}" for r in s.run("MATCH (p:Paper) WHERE p.year>0 RETURN (p.year/10)*10 AS d,count(p) AS c ORDER BY d")]
        tp=[f"  {r['n']}: {'█'*(r['c']//3)} {r['c']}" for r in s.run("MATCH (p:Paper)-[:MENTIONS_PROTEIN]->(pr:Protein) RETURN pr.name AS n,count(p) AS c ORDER BY c DESC LIMIT 5")]
        tm=[f"  {r['n']}: {'█'*(r['c']//2)} {r['c']}" for r in s.run("MATCH (p:Paper)-[:USES_METHOD]->(m:Method) RETURN m.name AS n,count(p) AS c ORDER BY c DESC LIMIT 5")]
        tc=[f"  {r['n']}: {'█'*(r['c']//5)} {r['c']}" for r in s.run("MATCH (p:Paper)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN c.name AS n,count(p) AS c ORDER BY c DESC LIMIT 5")]
    return f"**Overview**\n- Papers: **{pc}** | Proteins: **{prc}** | Genes: **{gc}**\n- Methods: **{mc}** | Concepts: **{cc}** | Organisms: **{oc}**\n- Relationships: **{rc}**\n\n**Papers by Decade**\n```\n"+"\n".join(dl)+"\n```\n\n**Top Proteins/Genes**\n```\n"+"\n".join(tp)+"\n```\n\n**Top Methods**\n```\n"+"\n".join(tm)+"\n```\n\n**Top Concepts**\n```\n"+"\n".join(tc)+"\n```"

def lookup_paper(ps):
    if not ps: return "Enter a paper number."
    pn=re.sub(r'[^0-9]','',str(ps))
    if not pn: return "Enter a valid number."
    with driver.session() as s:
        r=s.run("MATCH (p:Paper {number:$n}) OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) OPTIONAL MATCH (p)-[:USES_ORGANISM]->(o:Organism) RETURN p.title AS t,p.year AS y,p.journal AS j,p.authors AS a,p.key_findings AS f,collect(DISTINCT pr.name) AS pr,collect(DISTINCT m.name) AS me,collect(DISTINCT c.name) AS co,collect(DISTINCT o.name) AS og",n=pn).single()
        if not r: return f"Paper #{pn} not found."
        ti=r['t'] or 'Unknown'; url=f"https://scholar.google.com/scholar?q={'+'.join(ti.split()[:10])}"
        return f"**Paper #{pn}** ({r['y']})\n\n**Title:** {ti}\n**Journal:** {r['j'] or 'N/A'}\n**Authors:** {r['a'] or 'N/A'}\n**Findings:** {r['f'] or 'N/A'}\n**Proteins/Genes:** {', '.join(r['pr']) or 'None'}\n**Methods:** {', '.join(r['me']) or 'None'}\n**Concepts:** {', '.join(r['co']) or 'None'}\n**Organisms:** {', '.join(r['og']) or 'None'}\n\n[🔗 Google Scholar]({url})"

search_history={}; history_order=[]; current_lang="en"

def chat_fn(message, history):
    global search_history, history_order, current_lang
    conv=[h for h in history if isinstance(h,dict)]
    answer=generate_answer(message, conv, current_lang)
    skip=['hi','hello','hey','thanks','thank you','bye','goodbye','bonjour','salut','merci','au revoir','']
    if message.strip().lower() not in skip:
        search_history[message]=answer
        if message in history_order: history_order.remove(message)
        history_order.insert(0,message); history_order[:]=history_order[:20]
    return answer

def get_visible_history(): return history_order[:10]

SUG_EN=["How does Ras regulate cell migration?","Which papers use optogenetics?","What is the role of PTEN in chemotaxis?","Tell me about the Warburg effect","What methods study cell polarity?","Papers from the 2020s"]
SUG_FR=["Comment Ras régule-t-il la migration cellulaire ?","Quels articles utilisent l'optogénétique ?","Quel est le rôle de PTEN dans la chimiotaxie ?","Parlez-moi de l'effet Warburg","Quelles méthodes étudient la polarité cellulaire ?","Articles des années 2020"]

with gr.Blocks(title="Devreotes Lab Research Assistant") as demo:
    upload_entities_state=gr.State("")
    gr.HTML("""<div style="background:linear-gradient(135deg,#0a1628 0%,#1a3050 40%,#0f2440 70%,#0a1628 100%);padding:24px 32px;border-bottom:2px solid rgba(79,172,254,0.2);display:flex;align-items:center;justify-content:center;gap:20px;position:relative;overflow:hidden;"><div style="position:absolute;top:0;left:0;right:0;bottom:0;background:radial-gradient(ellipse at 20% 50%,rgba(79,172,254,0.08) 0%,transparent 60%),radial-gradient(ellipse at 80% 50%,rgba(120,200,255,0.05) 0%,transparent 50%);pointer-events:none;"></div><div style="position:relative;"><svg width="48" height="48" viewBox="0 0 48 48" fill="none"><circle cx="24" cy="24" r="22" stroke="#4facfe" stroke-width="1.5" opacity="0.3"/><circle cx="24" cy="24" r="16" stroke="#4facfe" stroke-width="1" opacity="0.2"/><circle cx="24" cy="24" r="4" fill="#4facfe" opacity="0.8"/><circle cx="24" cy="12" r="2.5" fill="#8ab4f8" opacity="0.6"/><circle cx="34" cy="20" r="2.5" fill="#8ab4f8" opacity="0.6"/><circle cx="34" cy="30" r="2.5" fill="#8ab4f8" opacity="0.6"/><circle cx="24" cy="36" r="2.5" fill="#8ab4f8" opacity="0.6"/><circle cx="14" cy="30" r="2.5" fill="#8ab4f8" opacity="0.6"/><circle cx="14" cy="20" r="2.5" fill="#8ab4f8" opacity="0.6"/><line x1="24" y1="24" x2="24" y2="12" stroke="#4facfe" stroke-width="0.8" opacity="0.4"/><line x1="24" y1="24" x2="34" y2="20" stroke="#4facfe" stroke-width="0.8" opacity="0.4"/><line x1="24" y1="24" x2="34" y2="30" stroke="#4facfe" stroke-width="0.8" opacity="0.4"/><line x1="24" y1="24" x2="24" y2="36" stroke="#4facfe" stroke-width="0.8" opacity="0.4"/><line x1="24" y1="24" x2="14" y2="30" stroke="#4facfe" stroke-width="0.8" opacity="0.4"/><line x1="24" y1="24" x2="14" y2="20" stroke="#4facfe" stroke-width="0.8" opacity="0.4"/></svg></div><div style="position:relative;text-align:center;"><div style="font-size:22px;font-weight:700;color:#e8f0fe;letter-spacing:-0.5px;font-family:'Georgia',serif;">Devreotes Lab Research Assistant</div><div style="font-size:12px;color:#4facfe;font-weight:500;letter-spacing:1px;margin-top:2px;text-transform:uppercase;">GraphRAG &middot; 238 Papers &middot; Johns Hopkins University School of Medicine</div><div style="font-size:10px;color:#5a7a9a;margin-top:4px;">🌐 English &amp; Français &middot; 🎤 Voice Input</div></div></div>""")
    with gr.Row():
        with gr.Column(scale=1,min_width=220):
            gr.HTML("""<div style="padding:12px;"><div style="color:#4facfe;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:12px;">Knowledge Graph</div><div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;"><div style="background:#111d30;border:1px solid rgba(79,172,254,0.12);border-radius:10px;padding:12px;text-align:center;"><div style="font-size:22px;font-weight:700;color:#4facfe;">238</div><div style="font-size:9px;color:#5a6d85;text-transform:uppercase;">Papers</div></div><div style="background:#111d30;border:1px solid rgba(79,172,254,0.12);border-radius:10px;padding:12px;text-align:center;"><div style="font-size:22px;font-weight:700;color:#4facfe;">738</div><div style="font-size:9px;color:#5a6d85;text-transform:uppercase;">Proteins</div></div><div style="background:#111d30;border:1px solid rgba(79,172,254,0.12);border-radius:10px;padding:12px;text-align:center;"><div style="font-size:22px;font-weight:700;color:#4facfe;">531</div><div style="font-size:9px;color:#5a6d85;text-transform:uppercase;">Methods</div></div><div style="background:#111d30;border:1px solid rgba(79,172,254,0.12);border-radius:10px;padding:12px;text-align:center;"><div style="font-size:22px;font-weight:700;color:#4facfe;">330</div><div style="font-size:9px;color:#5a6d85;text-transform:uppercase;">Concepts</div></div></div><div style="color:#4facfe;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;margin-top:16px;margin-bottom:8px;">Recent Searches</div></div>""")
            history_dropdown=gr.Dropdown(choices=[],label="Click to replay",interactive=True,allow_custom_value=False)
            gr.HTML("""<div style="padding:0 12px;"><div style="color:#4facfe;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;margin-top:16px;margin-bottom:8px;">📊 Analytics</div></div>""")
            analytics_btn=gr.Button("Load Analytics",size="sm",variant="secondary")
            analytics_display=gr.Markdown("")
        with gr.Column(scale=3):
            chatbot=gr.Chatbot(height=450,show_label=False)
            with gr.Row():
                msg=gr.Textbox(placeholder="Type or use 🎤...",show_label=False,scale=4)
                voice_input=gr.Audio(sources=["microphone"],type="filepath",label="🎤",scale=1)
                send_btn=gr.Button("Send",scale=1,variant="primary")
            with gr.Row():
                lang_toggle=gr.Radio(choices=["English","Français"],value="English",label="🌐 Language",scale=2)
                export_btn=gr.Button("📥 Export",size="sm",variant="secondary",scale=1)
                add_paper_btn=gr.Button("📤 Add Paper",size="sm",variant="secondary",scale=1)
            export_file=gr.File(label="Download Chat",visible=False)
            with gr.Group(visible=False) as upload_section:
                gr.HTML('<div style="color:#4facfe;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:8px;">Upload New Paper</div>')
                with gr.Row():
                    upload_file=gr.File(label="Select PDF",file_types=[".pdf"],scale=3)
                    upload_btn=gr.Button("🔍 Analyze",size="sm",variant="secondary",scale=1)
                upload_preview=gr.Markdown("")
                with gr.Row():
                    upload_password=gr.Textbox(placeholder="Password",type="password",show_label=False,scale=2)
                    confirm_btn=gr.Button("✅ Add to Graph",size="sm",variant="primary",visible=False,scale=1)
                upload_result=gr.Markdown("")
                close_upload_btn=gr.Button("✕ Close",size="sm",variant="secondary")
        with gr.Column(scale=1,min_width=220):
            gr.HTML("""<div style="padding:12px;"><div style="color:#4facfe;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:12px;">About the Lab</div><div style="background:#111d30;border:1px solid rgba(79,172,254,0.12);border-radius:12px;padding:14px;font-size:12px;color:#8a9bb8;line-height:1.6;"><strong style="color:#d0ddf0;">Prof. Peter N. Devreotes</strong><br>Department of Cell Biology<br>Johns Hopkins School of Medicine<br><br>Research spanning <strong style="color:#8ab4f8;">1975 &ndash; 2025</strong></div><div style="color:#4facfe;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;margin-top:16px;margin-bottom:8px;">🔎 Paper Finder</div></div>""")
            paper_input=gr.Textbox(placeholder="Paper # (e.g. 276)",show_label=False)
            paper_lookup_btn=gr.Button("Look Up Paper",size="sm",variant="secondary")
            paper_details=gr.Markdown("Enter a paper number.")
            gr.HTML('<div style="padding:0 12px;"><div style="color:#4facfe;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:1.5px;margin-top:16px;margin-bottom:8px;">💡 Try Asking</div></div>')
            s1=gr.Button(SUG_EN[0],size="sm"); s2=gr.Button(SUG_EN[1],size="sm"); s3=gr.Button(SUG_EN[2],size="sm")
            s4=gr.Button(SUG_EN[3],size="sm"); s5=gr.Button(SUG_EN[4],size="sm"); s6=gr.Button(SUG_EN[5],size="sm")

    def set_language(lc):
        global current_lang
        current_lang="fr" if lc=="Français" else "en"
        sg=SUG_FR if current_lang=="fr" else SUG_EN
        return tuple(gr.update(value=sg[i]) for i in range(6))
    lang_toggle.change(set_language,[lang_toggle],[s1,s2,s3,s4,s5,s6])

    def respond(message,ch):
        if not message or not message.strip(): return "",ch,gr.update(choices=get_visible_history(),value=None)
        bot=chat_fn(message,ch); ch.append({"role":"user","content":message}); ch.append({"role":"assistant","content":bot})
        return "",ch,gr.update(choices=get_visible_history(),value=None)
    def respond_voice(ap,ch):
        if not ap: return "",ch,gr.update(choices=get_visible_history(),value=None),None
        text,_=transcribe_audio(ap)
        if not text: return "",ch,gr.update(choices=get_visible_history(),value=None),None
        bot=chat_fn(text,ch); ch.append({"role":"user","content":f"🎤 {text}"}); ch.append({"role":"assistant","content":bot})
        return "",ch,gr.update(choices=get_visible_history(),value=None),None

    msg.submit(respond,[msg,chatbot],[msg,chatbot,history_dropdown])
    send_btn.click(respond,[msg,chatbot],[msg,chatbot,history_dropdown])
    voice_input.stop_recording(respond_voice,[voice_input,chatbot],[msg,chatbot,history_dropdown,voice_input])
    for btn in [s1,s2,s3,s4,s5,s6]:
        btn.click(fn=lambda sg,h:("",h+[{"role":"user","content":sg},{"role":"assistant","content":chat_fn(sg,h)}],gr.update(choices=get_visible_history(),value=None)),inputs=[btn,chatbot],outputs=[msg,chatbot,history_dropdown])
    def replay(sel,ch):
        if sel and sel in search_history: ch.append({"role":"user","content":sel}); ch.append({"role":"assistant","content":search_history[sel]})
        return ch,gr.update(value=None)
    history_dropdown.change(replay,[history_dropdown,chatbot],[chatbot,history_dropdown])
    add_paper_btn.click(fn=lambda:gr.update(visible=True),outputs=[upload_section])
    close_upload_btn.click(fn=lambda:gr.update(visible=False),outputs=[upload_section])
    def handle_upload(f):
        p,ej=process_upload(f)
        return (p,ej,gr.update(visible=True)) if ej else (p,"",gr.update(visible=False))
    upload_btn.click(handle_upload,[upload_file],[upload_preview,upload_entities_state,confirm_btn])
    confirm_btn.click(confirm_upload,[upload_entities_state,upload_password],[upload_result,confirm_btn])
    def handle_export(ch):
        p=export_chat(ch); return gr.update(value=p,visible=True) if p else gr.update(visible=False)
    export_btn.click(handle_export,[chatbot],[export_file])
    analytics_btn.click(load_analytics,outputs=[analytics_display])
    paper_lookup_btn.click(lookup_paper,[paper_input],[paper_details])

demo.launch(server_name="0.0.0.0",server_port=7860)