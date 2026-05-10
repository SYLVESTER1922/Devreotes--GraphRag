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
            resp = client.audio.transcriptions.create(model="whisper-1", file=f, response_format="verbose_json")
        return resp.text.strip(), getattr(resp, 'language', 'en') or 'en'
    except: return "", "en"

def correct_spelling(q):
    try:
        r = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"system","content":"Fix spelling errors in this biology question. Return ONLY the corrected question."},{"role":"user","content":q}], temperature=0, max_tokens=150)
        return r.choices[0].message.content.strip()
    except: return q

def extract_keywords(q):
    stops = {'what','which','where','when','how','does','about','tell','show','find','papers','paper','that','this','with','from','have','been','their','they','there','were','using','used','use','the','and','for','are','can','you','me','is','it','in','of','to','do','a','an','on','by','has','was','be','or','at','its','who','did','will','would','could','should','between','through','role','most','many','some','any','all','more','than','these','those','into','also','but','not','only','such','other','each','every','much','study','studies','research','describe','explain','discuss','related','involved','connection','connected','relationship','know','known','like','make','makes','give','list','name','please','want','need','think','things','compare','comparison','mentioned','mention','le','la','les','de','du','des','un','une','et','est','en','que','qui','dans','pour','sur','avec','ce','cette','sont','par','au','aux','ou','mais','pas','ne','ont','fait','plus','quels','quelle','quelles','quel','comment','quoi','pourquoi'}
    return [w for w in re.findall(r'[a-zA-Z0-9/()-]+', q) if w.lower() not in stops and len(w) > 2]

def query_graph(question):
    kws = extract_keywords(question); ql = question.lower(); ctx = []; seen = set()
    with driver.session() as s:
        pnums = re.findall(r'#(\d{1,3})\b', question)
        if not pnums: pnums = re.findall(r'(?:paper|papier|article)\s+(\d{1,3})\b', ql)
        for pn in (pnums or [])[:10]:
            for r in s.run("MATCH (p:Paper {number:$n}) OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) OPTIONAL MATCH (p)-[:USES_ORGANISM]->(o:Organism) RETURN p.number AS pn,p.title AS t,p.year AS y,p.key_findings AS f,p.journal AS j,p.authors AS a,collect(DISTINCT pr.name) AS pr,collect(DISTINCT m.name) AS me,collect(DISTINCT c.name) AS co,collect(DISTINCT o.name) AS og", n=pn):
                if r['pn'] not in seen: ctx.append(f"PAPER #{r['pn']} ({r['y']}): '{r['t']}'\n    Journal:{r['j']}\n    Authors:{r['a']}\n    Findings:{r['f']}\n    Proteins:{r['pr']}\n    Methods:{r['me']}\n    Concepts:{r['co']}\n    Organisms:{r['og']}"); seen.add(r['pn'])
        if any(w in ql for w in ['latest','newest','recent','most recent','plus récent','dernier','dernière']):
            for r in s.run("MATCH (p:Paper) WHERE p.year IS NOT NULL OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS pn,p.title AS t,p.year AS y,p.key_findings AS f,collect(DISTINCT pr.name) AS pr,collect(DISTINCT m.name) AS me,collect(DISTINCT c.name) AS co ORDER BY p.year DESC LIMIT 5"):
                ctx.append(f"  Paper #{r['pn']} ({r['y']}): '{r['t']}'\n    Findings:{r['f']}\n    Proteins:{r['pr']}\n    Methods:{r['me']}\n    Concepts:{r['co']}")
        if any(w in ql for w in ['oldest','earliest','first paper','premier','ancien']):
            for r in s.run("MATCH (p:Paper) WHERE p.year IS NOT NULL AND p.year>0 OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS pn,p.title AS t,p.year AS y,p.key_findings AS f,collect(DISTINCT pr.name) AS pr,collect(DISTINCT c.name) AS co ORDER BY p.year ASC LIMIT 5"):
                ctx.append(f"  Paper #{r['pn']} ({r['y']}): '{r['t']}'\n    Findings:{r['f']}\n    Proteins:{r['pr']}\n    Concepts:{r['co']}")
        dm = re.search(r'\b(19[7-9]0|20[0-2]0)s?\b', ql); ym = re.search(r'\b(19[7-9]\d|20[0-2]\d)\b', question)
        if dm:
            dec=int(dm.group(1)[:4]); recs=list(s.run("MATCH (p:Paper) WHERE p.year>=$s AND p.year<$e OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS pn,p.title AS t,p.year AS y,collect(DISTINCT c.name) AS co ORDER BY p.year LIMIT 15",s=dec,e=dec+10))
            if recs: ctx.append(f"PAPERS FROM THE {dec}s ({len(recs)} found):"); [ctx.append(f"  Paper #{r['pn']} ({r['y']}): '{r['t']}' — {r['co']}") for r in recs if r['pn'] not in seen and not seen.add(r['pn'])]
        elif ym and not dm:
            yr=int(ym.group(1)); recs=list(s.run("MATCH (p:Paper {year:$y}) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS pn,p.title AS t,p.year AS y,collect(DISTINCT c.name) AS co ORDER BY p.year LIMIT 15",y=yr))
            if recs: ctx.append(f"PAPERS FROM {yr} ({len(recs)} found):"); [ctx.append(f"  Paper #{r['pn']} ({r['y']}): '{r['t']}' — {r['co']}") for r in recs if r['pn'] not in seen and not seen.add(r['pn'])]
        if any(p in ql for p in ['list all','show all','all proteins','all methods','all concepts','all organisms','all genes','top proteins','top methods','top concepts','top genes','most studied','most used','most common','frequently','toutes les','tous les']):
            if any(w in ql for w in ['protein','gene','protéine','gène']):
                recs=list(s.run("MATCH (p:Paper)-[:MENTIONS_PROTEIN]->(pr:Protein) RETURN pr.name AS n,count(p) AS c ORDER BY c DESC LIMIT 20"))
                if recs: ctx.append("TOP PROTEINS/GENES:"); [ctx.append(f"  {r['n']}: {r['c']} papers") for r in recs]
            elif any(w in ql for w in ['method','technique','méthode']):
                recs=list(s.run("MATCH (p:Paper)-[:USES_METHOD]->(m:Method) RETURN m.name AS n,count(p) AS c ORDER BY c DESC LIMIT 20"))
                if recs: ctx.append("TOP METHODS:"); [ctx.append(f"  {r['n']}: {r['c']} papers") for r in recs]
            elif any(w in ql for w in ['concept','topic','sujet']):
                recs=list(s.run("MATCH (p:Paper)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN c.name AS n,count(p) AS c ORDER BY c DESC LIMIT 20"))
                if recs: ctx.append("TOP CONCEPTS:"); [ctx.append(f"  {r['n']}: {r['c']} papers") for r in recs]
            elif any(w in ql for w in ['organism','species','organisme']):
                recs=list(s.run("MATCH (p:Paper)-[:USES_ORGANISM]->(o:Organism) RETURN o.name AS n,count(p) AS c ORDER BY c DESC LIMIT 20"))
                if recs: ctx.append("TOP ORGANISMS:"); [ctx.append(f"  {r['n']}: {r['c']} papers") for r in recs]
            else:
                for lb,rl,nd in [("PROTEINS","MENTIONS_PROTEIN","Protein"),("METHODS","USES_METHOD","Method"),("CONCEPTS","EXPLORES_CONCEPT","Concept")]:
                    recs=list(s.run(f"MATCH (p:Paper)-[:{rl}]->(n:{nd}) RETURN n.name AS n,count(p) AS c ORDER BY c DESC LIMIT 10"))
                    if recs: ctx.append(f"TOP {lb}:"); [ctx.append(f"  {r['n']}: {r['c']} papers") for r in recs]
        if any(p in ql for p in ['connected to','connection between','related to','relationship between','link between','lien entre','connexion entre']):
            if len(kws)>=2:
                k1,k2=kws[0].lower(),kws[1].lower()
                recs=list(s.run("MATCH (p:Paper)-[]->(e1) WHERE toLower(e1.name) CONTAINS $k1 WITH p MATCH (p)-[]->(e2) WHERE toLower(e2.name) CONTAINS $k2 OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS pn,p.title AS t,p.year AS y,p.key_findings AS f,collect(DISTINCT pr.name) AS pr,collect(DISTINCT c.name) AS co LIMIT 10",k1=k1,k2=k2))
                if recs: ctx.append(f"PAPERS CONNECTING '{kws[0]}' AND '{kws[1]}':"); [ctx.append(f"  Paper #{r['pn']} ({r['y']}): '{r['t']}'\n    Findings:{r['f']}\n    Proteins:{r['pr']}\n    Concepts:{r['co']}") for r in recs if r['pn'] not in seen and not seen.add(r['pn'])]
        for kw in kws:
            kl=kw.lower(); terms=[kl]+([ kl[1:], kl[:-1]] if len(kl)>=5 else [])
            for term in terms:
                found=False
                for rl,nd,lb in [("MENTIONS_PROTEIN","Protein","protein"),("MENTIONS_GENE","Gene","gene"),("USES_METHOD","Method","method"),("EXPLORES_CONCEPT","Concept","concept"),("USES_ORGANISM","Organism","organism")]:
                    recs=list(s.run(f"MATCH (p:Paper)-[r:{rl}]->(n:{nd}) WHERE toLower(n.name) CONTAINS $q RETURN p.number AS pn,p.title AS t,p.year AS y,n.name AS e,r.context AS c ORDER BY p.year DESC LIMIT 10",q=term))
                    if recs: ctx.append(f"\n{lb.upper()} MATCHES for '{kw}':"); [ctx.append(f"  Paper #{r['pn']} ({r['y']}): '{r['t']}' — {r['e']}: {r['c']}") for r in recs if r['pn'] not in seen and not seen.add(r['pn'])]; found=True; break
                if found: break
        for kw in kws:
            recs=list(s.run("MATCH (p:Paper) WHERE toLower(p.title) CONTAINS $q OR toLower(p.key_findings) CONTAINS $q OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) RETURN p.number AS pn,p.title AS t,p.year AS y,p.key_findings AS f,collect(DISTINCT pr.name) AS pr,collect(DISTINCT m.name) AS me,collect(DISTINCT c.name) AS co LIMIT 5",q=kw.lower()))
            nr=[r for r in recs if r['pn'] not in seen]
            if nr: ctx.append(f"\nPAPER MATCHES for '{kw}':"); [ctx.append(f"  Paper #{r['pn']} ({r['y']}): '{r['t']}'\n    Findings:{r['f']}\n    Proteins:{r['pr']}\n    Methods:{r['me']}") for r in nr if not seen.add(r['pn'])]
        if any(w in ql for w in ['how many','total','overview','statistics','stat','combien','statistiques']):
            counts={}
            for lb in ['Paper','Protein','Gene','Method','Concept','Organism']: counts[lb]=s.run(f"MATCH (n:{lb}) RETURN count(n) AS c").single()["c"]
            counts['Rel']=s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            ctx.append(f"\nSTATISTICS: Papers:{counts['Paper']} Proteins:{counts['Protein']} Genes:{counts['Gene']} Methods:{counts['Method']} Concepts:{counts['Concept']} Organisms:{counts['Organism']} Relationships:{counts['Rel']}")
    return "\n".join(ctx) if ctx else "NO_RESULTS"

def get_sys_prompt(lang):
    b="""You are the Devreotes Lab Research Assistant for Prof. Peter Devreotes' lab at Johns Hopkins University School of Medicine.
The knowledge graph contains research papers (1975-2025), proteins/genes, methods, concepts, and organisms.
RULES: 1.Answer from graph context only. 2.Concise:2-4 sentences. 3.Cite paper numbers. 4.Say not available if missing. 5.Always cite papers. 6.No outside knowledge. 7.Brief descriptions. 8.Highlight connections. 9.Use history for follow-ups. 10.Summarize when asked.
11.IMPORTANT: End every answer with exactly 3 follow-up suggestions. Format each on its own line starting with "FOLLOWUP:" (this prefix will be hidden from the user). Make them brief, specific, and relevant."""
    if lang=="fr": b+="\n12.RESPOND IN FRENCH. Use 'Article #' not 'Paper #'. Follow-ups in French."
    return b

GREET_EN="Hello! I'm the Devreotes Lab Research Assistant — ask me anything about Professor Devreotes' published papers on cell migration, chemotaxis, and signal transduction."
GREET_FR="Bonjour ! Je suis l'assistant de recherche du laboratoire Devreotes — posez-moi n'importe quelle question sur les articles publiés."
GW_EN={'hi','hello','hey','good morning','good afternoon','good evening','howdy','greetings','sup','yo',"what's up",'whats up'}
GW_FR={'bonjour','salut','bonsoir','coucou'}

def parse_followups(answer):
    lines=answer.split('\n'); main=[]; fups=[]
    for line in lines:
        stripped=line.strip()
        if not stripped:
            main.append(line)
            continue
        if stripped.startswith('FOLLOWUP:'):
            fup=stripped.replace('FOLLOWUP:','').strip()
            if fup: fups.append(fup)
        elif stripped.startswith('💡'):
            fup=stripped.replace('💡','').strip()
            if fup: fups.append(fup)
        elif stripped.startswith('- 💡'):
            fup=stripped.replace('- 💡','').strip()
            if fup: fups.append(fup)
        elif stripped.startswith('*💡'):
            fup=stripped.replace('*💡','').strip().rstrip('*')
            if fup: fups.append(fup)
        elif len(fups) > 0 and len(fups) < 3 and stripped.startswith(('- ','* ','1.','2.','3.')):
            fup=re.sub(r'^[-*\d.]+\s*', '', stripped).strip()
            if fup and len(fup) > 10: fups.append(fup)
        else:
            main.append(line)
    clean='\n'.join(main).strip()
    return clean, fups[:3]

def generate_answer(question, conv_hist=None, lang="en"):
    if not question or not question.strip(): return ("Veuillez taper une question." if lang=="fr" else "Please type a question."), []
    q=question.strip().lower().rstrip('!?.,:;')
    if q in GW_EN or any(q.startswith(g) for g in GW_EN):
        gr_msg = GREET_FR if lang=="fr" else GREET_EN
        if lang=="fr": return gr_msg, ["Comment Ras régule-t-il la migration cellulaire ?","Quelle est la protéine la plus étudiée ?","Quels articles utilisent l'optogénétique ?"]
        return gr_msg, ["How does Ras regulate cell migration?","What is the most studied protein?","Which papers use optogenetics?"]
    if q in GW_FR or any(q.startswith(g) for g in GW_FR):
        return GREET_FR, ["Comment Ras régule-t-il la migration cellulaire ?","Quelle est la protéine la plus étudiée ?","Quels articles utilisent l'optogénétique ?"]
    if any(t in q for t in ['how are you','who are you','what are you','your name','what can you do','help me','comment allez','qui êtes','aide']):
        if lang=="fr": return "Je suis l'assistant du laboratoire Devreotes. Je réponds à vos questions sur les articles de recherche (1975-2025).", ["Qu'est-ce que PTEN ?","Articles des années 2020 ?","Combien de protéines ?"]
        return "I'm the Devreotes Lab Research Assistant. I answer questions about research papers spanning 1975 to 2025.", ["What is PTEN?","Papers from the 2020s?","How many proteins?"]
    if any(w in q for w in ['thank','merci']): return ("De rien !" if lang=="fr" else "You're welcome!"), []
    if any(w in q for w in ['bye','goodbye','au revoir']): return ("Au revoir !" if lang=="fr" else "Goodbye!"), []

    if len(q.split()) <= 1 and q not in ['statistics','overview','stats']:
        if lang=="fr": return "Pourriez-vous reformuler votre question ?", []
        return "Could you please rephrase your question?", []

    msgs=[{"role":"system","content":get_sys_prompt(lang)}]
    if conv_hist:
        for t in conv_hist[-8:]: msgs.append(t)
    is_conv=False
    if conv_hist:
        ref=['this','these','those','them','they','it','that','above','previous','mentioned','you said','the same','where is','where are','when was','when were','who wrote','more about','tell me more','elaborate','go on','continue','what else','which ones','expand on','ceci','cela','ces','eux','ci-dessus','précédent','mentionné','plus de détails','continuer']
        summ=['summarize','summary','in short','briefly','short version','simplify','shorter','résumer','résumé','brièvement','en bref']
        if any(w in q for w in summ): is_conv=True
        elif len(question.split())<=8 and any(w in q for w in ref): is_conv=True
    if is_conv:
        msgs.append({"role":"user","content":question})
        ans=client.chat.completions.create(model="gpt-4o-mini",messages=msgs,temperature=0.3,max_tokens=800).choices[0].message.content
        return parse_followups(ans)
    corrected=correct_spelling(question) if len(question.split())>4 else question
    gc=query_graph(corrected)
    if gc=="NO_RESULTS":
        if conv_hist:
            msgs.append({"role":"user","content":question})
            ans=client.chat.completions.create(model="gpt-4o-mini",messages=msgs,temperature=0.3,max_tokens=800).choices[0].message.content
            main,fups=parse_followups(ans)
            if not any(w in main.lower() for w in ['not available','pas disponible']): return main,fups
        na="Cette information n'est pas disponible." if lang=="fr" else "This information is not available in the Devreotes Lab knowledge base."
        return na, []
    msgs.append({"role":"user","content":f"Based on graph data, answer:\n\nGRAPH CONTEXT:\n{gc}\n\nQUESTION: {question}"})
    ans=client.chat.completions.create(model="gpt-4o-mini",messages=msgs,temperature=0.3,max_tokens=800).choices[0].message.content
    return parse_followups(ans)

# PAPER UPLOAD
EXT_PROMPT="""Extract entities from this biomedical paper as JSON:
{"title":"","authors":[],"year":0,"journal":"","proteins":[{"name":"","context":""}],"methods":[{"name":"","context":""}],"concepts":[{"name":"","context":""}],"model_organisms":[{"name":"","context":""}],"key_findings":"","is_cell_biology":true}
Return ONLY valid JSON."""

def ext_pdf(fp): d=fitz.open(fp); t="".join(p.get_text() for p in d); d.close(); return t.strip()
def ext_ent(text):
    t=text[:15000]+("\n...\n"+text[-3000:] if len(text)>15000 else "")
    try: return json.loads(client.chat.completions.create(model="gpt-4o-mini",messages=[{"role":"system","content":EXT_PROMPT},{"role":"user","content":t}],max_tokens=3000,temperature=0,response_format={"type":"json_object"}).choices[0].message.content)
    except: return None
def esc(t): return t.replace("\\","\\\\").replace("'","\\'").replace('"','\\"') if t else ""

def load_to_graph(pn,e):
    st=[f"MERGE (p:Paper {{number:'{pn}'}}) SET p.title='{esc(e.get('title',''))}',p.year={e.get('year',0)},p.journal='{esc(e.get('journal',''))}',p.key_findings='{esc(e.get('key_findings',''))}',p.authors='{esc(', '.join(e.get('authors',[])))}'"]
    for x in e.get("proteins",[]):
        n=esc(x["name"]); c=esc(x.get("context",""))
        st.append(f"MERGE (pr:Protein {{name:'{n}'}}) WITH pr MATCH (p:Paper {{number:'{pn}'}}) MERGE (p)-[:MENTIONS_PROTEIN {{context:'{c}'}}]->(pr)")
        st.append(f"MERGE (g:Gene {{name:'{n}'}}) WITH g MATCH (p:Paper {{number:'{pn}'}}) MERGE (p)-[:MENTIONS_GENE {{context:'{c}'}}]->(g)")
    for x in e.get("methods",[]): st.append(f"MERGE (m:Method {{name:'{esc(x['name'])}'}}) WITH m MATCH (p:Paper {{number:'{pn}'}}) MERGE (p)-[:USES_METHOD {{context:'{esc(x.get('context',''))}'}}]->(m)")
    for x in e.get("concepts",[]): st.append(f"MERGE (c:Concept {{name:'{esc(x['name'])}'}}) WITH c MATCH (p:Paper {{number:'{pn}'}}) MERGE (p)-[:EXPLORES_CONCEPT {{context:'{esc(x.get('context',''))}'}}]->(c)")
    for x in e.get("model_organisms",[]): st.append(f"MERGE (o:Organism {{name:'{esc(x['name'])}'}}) WITH o MATCH (p:Paper {{number:'{pn}'}}) MERGE (p)-[:USES_ORGANISM {{context:'{esc(x.get('context',''))}'}}]->(o)")
    errs=[]
    with driver.session() as ss:
        for s2 in st:
            try: ss.run(s2)
            except Exception as ex: errs.append(str(ex))
    return len(st),errs

def next_pn():
    with driver.session() as s:
        r=s.run("MATCH (p:Paper) RETURN toInteger(p.number) AS n ORDER BY n DESC LIMIT 1").single()
        return str(r["n"]+1) if r and r["n"] else "239"

def process_upload(file):
    if not file: return "❌ No file.",""
    try: text=ext_pdf(file.name)
    except Exception as ex: return f"❌ {ex}",""
    if len(text)<200: return "❌ Not enough text.",""
    ent=ext_ent(text)
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
        if dups: return '<span style="color:#c9a84c;">⚠️ Similar exists:</span><br>'+"\n".join([f"  **#{d['n']}** ({d['y']}): {d['t']}" for d in dups])+"\n**NOT added.**",gr.update(visible=True)
    pn=next_pn(); _,errs=load_to_graph(pn,ent)
    np=len(ent.get("proteins",[])); nm=len(ent.get("methods",[])); nc=len(ent.get("concepts",[])); no=len(ent.get("model_organisms",[]))
    return f'<span style="color:#c9a84c;">✅ Paper #{pn} added! {np} proteins, {nm} methods, {nc} concepts, {no} organisms.</span>',gr.update(visible=False)

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
    return f"**Overview**\n\n**Papers:** **{pc}** | **Proteins:** **{prc}** | **Genes:** **{gc}**\n\n**Methods:** **{mc}** | **Concepts:** **{cc}** | **Organisms:** **{oc}**\n\n**Relationships:** **{rc}**\n\n**Papers by Decade**\n```\n"+"\n".join(dl)+"\n```\n\n**Top Proteins/Genes**\n```\n"+"\n".join(tp)+"\n```\n\n**Top Methods**\n```\n"+"\n".join(tm)+"\n```\n\n**Top Concepts**\n```\n"+"\n".join(tc)+"\n```"

def lookup_paper(ps):
    if not ps: return "Enter a paper number."
    pn=re.sub(r'[^0-9]','',str(ps))
    if not pn: return "Invalid number."
    with driver.session() as s:
        r=s.run("MATCH (p:Paper {number:$n}) OPTIONAL MATCH (p)-[:MENTIONS_PROTEIN]->(pr:Protein) OPTIONAL MATCH (p)-[:USES_METHOD]->(m:Method) OPTIONAL MATCH (p)-[:EXPLORES_CONCEPT]->(c:Concept) OPTIONAL MATCH (p)-[:USES_ORGANISM]->(o:Organism) RETURN p.title AS t,p.year AS y,p.journal AS j,p.authors AS a,p.key_findings AS f,collect(DISTINCT pr.name) AS pr,collect(DISTINCT m.name) AS me,collect(DISTINCT c.name) AS co,collect(DISTINCT o.name) AS og",n=pn).single()
        if not r: return f"Paper #{pn} not found."
        ti=r['t'] or '?'; url=f"https://scholar.google.com/scholar?q={'+'.join(ti.split()[:10])}"
        return f"**Paper #{pn}** ({r['y']})\n\n**Title:** {ti}\n**Journal:** {r['j'] or 'N/A'}\n**Authors:** {r['a'] or 'N/A'}\n**Findings:** {r['f'] or 'N/A'}\n**Proteins:** {', '.join(r['pr']) or 'None'}\n**Methods:** {', '.join(r['me']) or 'None'}\n**Concepts:** {', '.join(r['co']) or 'None'}\n**Organisms:** {', '.join(r['og']) or 'None'}\n\n[🔗 Google Scholar]({url})"

# DYNAMIC GRAPH COUNTS
def get_graph_counts():
    try:
        with driver.session() as s:
            counts = {}
            for lb in ['Paper','Protein','Method','Concept']:
                counts[lb] = s.run(f"MATCH (n:{lb}) RETURN count(n) AS c").single()["c"]
            return counts
    except:
        return {'Paper':'—','Protein':'—','Method':'—','Concept':'—'}

search_history={}; history_order=[]; current_lang="en"

def chat_fn(message, history):
    global search_history, history_order, current_lang
    conv=[h for h in history if isinstance(h,dict)]
    answer, followups = generate_answer(message, conv, current_lang)
    skip=['hi','hello','hey','thanks','thank you','bye','goodbye','bonjour','salut','merci','au revoir','']
    if message.strip().lower() not in skip:
        search_history[message]=answer
        if message in history_order: history_order.remove(message)
        history_order.insert(0,message); history_order[:]=history_order[:20]
    return answer, followups

def get_vis_hist(): return history_order[:10]

# ============================================================
# UI — Navy + Gold Academic Theme
# ============================================================
with gr.Blocks(title="Devreotes Lab Research Assistant") as demo:
    upload_entities_state=gr.State("")
    followup_state=gr.State([])

    # RUNTIME CSS — Chat bubble styling
    gr.HTML("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Lato:wght@300;400;700&display=swap');

        /* Base */
        body, .gradio-container {
            background: #050d1a !important;
            font-family: 'Lato', sans-serif !important;
            color: #ffffff !important;
        }

        /* ALL TEXT WHITE by default */
        p, span, div, label, li, td, th, dt, dd,
        .prose, .markdown-text, .label-wrap,
        input, textarea, select, option,
        [class*="svelte"], .wrap, .container,
        [data-testid] span, [data-testid] label,
        [data-testid] p, [data-testid] div { 
            color: #ffffff !important; 
        }

        /* Gold for headings, bold, strong */
        h1, h2, h3, h4, h5, h6, strong, b { color: #c9a84c !important; }

        /* User chat bubbles */
        .message.user > div, div[class*="message"][class*="user"] > div:last-child {
            background: linear-gradient(135deg, #0d2147, #162d5a) !important;
            color: #ffffff !important;
            border: 1px solid rgba(201,168,76,0.15) !important;
            border-radius: 16px 16px 4px 16px !important;
            padding: 12px 16px !important;
        }

        /* Bot chat bubbles */
        .message.bot > div, .message.assistant > div,
        div[class*="message"][class*="bot"] > div:last-child,
        div[class*="message"][class*="assistant"] > div:last-child {
            background: linear-gradient(135deg, #091428, #0d1b2e) !important;
            color: #ffffff !important;
            border: 1px solid rgba(201,168,76,0.1) !important;
            border-radius: 16px 16px 16px 4px !important;
            padding: 12px 16px !important;
        }

        /* Bold/strong in chat = gold */
        .message strong, div[class*="message"] strong { color: #c9a84c !important; }

        /* Links = light gold */
        a, .message a, div[class*="message"] a { color: #e8d48b !important; text-decoration: underline !important; }

        /* Code blocks in analytics = white text on dark bg */
        pre, code, pre *, code * { 
            color: #ffffff !important; 
            background: #0a1628 !important; 
        }

        /* Input fields */
        input, textarea, .textbox textarea {
            background: #0a1628 !important;
            color: #ffffff !important;
            border: 1px solid rgba(201,168,76,0.2) !important;
            border-radius: 10px !important;
        }

        /* Secondary buttons = gold text */
        button.secondary, button[class*="secondary"] {
            background: linear-gradient(135deg, #0d1b2e, #162640) !important;
            color: #c9a84c !important;
            border: 1px solid rgba(201,168,76,0.2) !important;
            border-radius: 8px !important;
        }
        button.secondary:hover, button[class*="secondary"]:hover {
            border-color: #c9a84c !important;
        }

        /* Primary button = dark text on gold */
        button.primary, button[class*="primary"] {
            background: linear-gradient(135deg, #8a6d1b, #c9a84c) !important;
            color: #0c1a2e !important;
            border: none !important;
            border-radius: 8px !important;
            font-weight: 700 !important;
        }

        /* Chatbot container */
        .chatbot, div[class*="chatbot"] {
            background: #070e1a !important;
            border: 1px solid rgba(201,168,76,0.1) !important;
            border-radius: 16px !important;
        }

        /* Scrollbar */
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-thumb { background: #c9a84c; border-radius: 4px; }

        /* File upload area */
        .upload-text, .file-preview, [class*="upload"] span,
        [class*="file"] span, [class*="drop"] span { color: #ffffff !important; }

        /* Audio/voice component */
        [class*="audio"] *, [data-testid*="audio"] * { color: #ffffff !important; }

        /* Dropdown */
        [class*="dropdown"] * { color: #ffffff !important; }

        /* SVG icons in chat (copy, delete) - keep them visible */
        button svg { color: #8a9bb8 !important; fill: #8a9bb8 !important; }
        button:hover svg { color: #c9a84c !important; fill: #c9a84c !important; }

        /* Follow-up buttons */
        button[class*="sm"] { font-size: 12px !important; }

        /* Hide Gradio footer */
        footer { display: none !important; }

        /* FORCE dark backgrounds on all Gradio containers */
        .gradio-container, .gr-box, .gr-panel, .gr-form, .gr-input-label,
        .gr-block, .gr-padded, .contain, .gap, .form,
        [class*="block"], [class*="panel"], [class*="form"], [class*="group"],
        [class*="wrap"], [class*="container"], [class*="input"],
        [class*="textbox"], [class*="dropdown"], [class*="audio"],
        [class*="file"], [class*="upload"], [class*="accordion"],
        .secondary-wrap, .primary-wrap {
            background: #050d1a !important;
            background-color: #050d1a !important;
        }

        /* Dropdown menu items */
        [class*="dropdown"] ul, [class*="dropdown"] li,
        [class*="dropdown"] option, [role="listbox"],
        [role="option"], .options, ul[class*="options"] {
            background: #0a1628 !important;
            color: #ffffff !important;
        }

        /* Audio record button label */
        [class*="audio"] label, [class*="audio"] span,
        [class*="audio"] p, [class*="audio"] div,
        [class*="record"] *, [class*="mic"] * {
            color: #ffffff !important;
        }

        /* File upload drop zone */
        [class*="upload"], [class*="drop-zone"], [class*="file-upload"],
        [class*="upload"] *, [class*="file"] div,
        [class*="file"] span, [class*="file"] p {
            background: #0a1628 !important;
            color: #ffffff !important;
            border-color: rgba(201,168,76,0.2) !important;
        }

        /* Search history dropdown */
        [class*="dropdown"] input, [class*="dropdown"] div,
        [class*="dropdown"] span, [class*="dropdown"] label,
        [class*="dropdown"] button {
            background: #0a1628 !important;
            color: #ffffff !important;
        }

        /* Upload preview and result areas */
        [class*="markdown"], [class*="html"],
        [class*="markdown"] *, [class*="html"] * {
            color: #ffffff !important;
        }
        [class*="markdown"] strong, [class*="html"] strong {
            color: #c9a84c !important;
        }

        /* Warning/success messages */
        .message-wrap, [class*="message-wrap"],
        [class*="info"], [class*="warning"], [class*="error"] {
            background: #0a1628 !important;
            color: #ffffff !important;
        }

        /* Accordion/Group backgrounds */
        details, summary, [class*="accordion"],
        [class*="group"], fieldset {
            background: #050d1a !important;
            color: #ffffff !important;
        }
        /* Microphone button and label */
        audio, .audio-label, [data-testid="audio"] *,
        [class*="audio"] label span, [class*="audio"] button span,
        .microphone *, [aria-label*="audio"] *,
        [class*="waveform"] *, [class*="player"] *,
        .audio-container *, .audio-input * {
            color: #ffffff !important;
            background-color: #050d1a !important;
        }
        [class*="audio"] button {
            color: #c9a84c !important;
            background: #0a1628 !important;
        }
    </style>
    """)

    # HEADER
    gr.HTML("""
        <div style="background: linear-gradient(135deg, #0c1a2e 0%, #1a2d4a 30%, #243b5c 60%, #0c1a2e 100%);
                    padding: 28px 36px; border-bottom: 3px solid #c9a84c;
                    display: flex; align-items: center; justify-content: center; gap: 24px;
                    position: relative; overflow: hidden;">
            <div style="position:absolute; top:0; left:0; right:0; bottom:0;
                        background: radial-gradient(ellipse at 30% 50%, rgba(201,168,76,0.06) 0%, transparent 60%),
                                    radial-gradient(ellipse at 70% 50%, rgba(201,168,76,0.04) 0%, transparent 50%);
                        pointer-events: none;"></div>
            <div style="position: relative;">
                <svg width="52" height="52" viewBox="0 0 52 52" fill="none">
                    <circle cx="26" cy="26" r="24" stroke="#c9a84c" stroke-width="1.5" opacity="0.4"/>
                    <circle cx="26" cy="26" r="17" stroke="#c9a84c" stroke-width="1" opacity="0.25"/>
                    <circle cx="26" cy="26" r="4.5" fill="#c9a84c" opacity="0.9"/>
                    <circle cx="26" cy="11" r="2.5" fill="#e8d48b" opacity="0.7"/>
                    <circle cx="38" cy="20" r="2.5" fill="#e8d48b" opacity="0.7"/>
                    <circle cx="38" cy="32" r="2.5" fill="#e8d48b" opacity="0.7"/>
                    <circle cx="26" cy="41" r="2.5" fill="#e8d48b" opacity="0.7"/>
                    <circle cx="14" cy="32" r="2.5" fill="#e8d48b" opacity="0.7"/>
                    <circle cx="14" cy="20" r="2.5" fill="#e8d48b" opacity="0.7"/>
                    <line x1="26" y1="26" x2="26" y2="11" stroke="#c9a84c" stroke-width="0.8" opacity="0.35"/>
                    <line x1="26" y1="26" x2="38" y2="20" stroke="#c9a84c" stroke-width="0.8" opacity="0.35"/>
                    <line x1="26" y1="26" x2="38" y2="32" stroke="#c9a84c" stroke-width="0.8" opacity="0.35"/>
                    <line x1="26" y1="26" x2="26" y2="41" stroke="#c9a84c" stroke-width="0.8" opacity="0.35"/>
                    <line x1="26" y1="26" x2="14" y2="32" stroke="#c9a84c" stroke-width="0.8" opacity="0.35"/>
                    <line x1="26" y1="26" x2="14" y2="20" stroke="#c9a84c" stroke-width="0.8" opacity="0.35"/>
                </svg>
            </div>
            <div style="position: relative; text-align: center;">
                <div style="font-size: 28px; font-weight: 700; color: #c9a84c !important;
                            letter-spacing: 0.5px; font-family: 'Palatino Linotype', 'Book Antiqua', Palatino, Georgia, serif;">
                    Devreotes Lab Research Assistant</div>
                <div style="font-size: 11px; color: #c9a84c; font-weight: 600;
                            letter-spacing: 2px; margin-top: 4px; text-transform: uppercase;">
                    GraphRAG &middot; Research Papers &middot; Johns Hopkins University School of Medicine</div>
                <div style="font-size: 10px; color: #8a9bb8; margin-top: 5px; letter-spacing: 0.5px;">
                    🌐 English &amp; Français &middot; 🎤 Voice Input</div>
            </div>
        </div>
    """)

    with gr.Row():
        # LEFT SIDEBAR
        with gr.Column(scale=1, min_width=230):
            gr.HTML("""<div style="padding: 12px;">
                <div style="color: #c9a84c; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 12px;">Knowledge Graph</div>
            </div>""")
            stats_display = gr.Markdown("Loading stats...")
            gr.HTML("""<div style="padding: 0 12px;">
                <div style="color: #c9a84c; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 2px; margin-top: 20px; margin-bottom: 8px;">Recent Searches</div>
            </div>""")
            history_dropdown=gr.Dropdown(choices=[],label="Click to replay",interactive=True,allow_custom_value=False)
            gr.HTML('<div style="padding:0 12px;"><div style="color:#c9a84c;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:2px;margin-top:18px;margin-bottom:8px;">🛠 Tools</div></div>')
            export_btn=gr.Button("📥 Export Chat",size="sm",variant="secondary")
            export_file=gr.File(label="Download",visible=False)
            add_paper_btn=gr.Button("📤 Add Paper",size="sm",variant="secondary")
            with gr.Group(visible=False) as upload_section:
                upload_file=gr.File(label="Select PDF",file_types=[".pdf"])
                upload_btn=gr.Button("🔍 Analyze",size="sm",variant="secondary")
                upload_preview=gr.Markdown("")
                with gr.Row():
                    upload_password=gr.Textbox(placeholder="Password",type="password",show_label=False,scale=2)
                    confirm_btn=gr.Button("✅ Add",size="sm",variant="primary",visible=False,scale=1)
                upload_result=gr.HTML("")
                close_upload_btn=gr.Button("✕ Close",size="sm",variant="secondary")
            gr.HTML('<div style="padding:0 12px;"><div style="color:#c9a84c;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:2px;margin-top:18px;margin-bottom:8px;">📊 Analytics</div></div>')
            analytics_btn=gr.Button("Load Analytics",size="sm",variant="secondary")
            analytics_display=gr.Markdown("")

        # CENTER CHAT
        with gr.Column(scale=3):
            chatbot=gr.Chatbot(height=460,show_label=False)
            with gr.Row():
                msg=gr.Textbox(placeholder="Ask a question (type or use 🎤)...",show_label=False,scale=4)
                voice_input=gr.Audio(sources=["microphone"],type="filepath",label="🎤",scale=1)
                send_btn=gr.Button("Send",scale=1,variant="primary")
            with gr.Row():
                lang_en=gr.Button("🇬🇧 English",size="sm",variant="secondary")
                lang_fr=gr.Button("🇫🇷 Français",size="sm",variant="secondary")
            gr.HTML('<div style="color:#c9a84c;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:2px;margin-top:8px;margin-bottom:6px;">💡 Suggested Follow-ups</div>')
            fup1=gr.Button("How does Ras regulate cell migration?",size="sm",visible=True)
            fup2=gr.Button("What is the most studied protein?",size="sm",visible=True)
            fup3=gr.Button("Which papers use optogenetics?",size="sm",visible=True)

        # RIGHT SIDEBAR
        with gr.Column(scale=1, min_width=220):
            gr.HTML("""<div style="padding: 12px;">
                <div style="color: #c9a84c; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 12px;">About the Lab</div>
                <div style="background: linear-gradient(135deg, #0f1c30, #162640); border: 1px solid rgba(201,168,76,0.15);
                            border-radius: 12px; padding: 16px; font-size: 12px; color: #8a9bb8; line-height: 1.7;">
                    <strong style="color: #c9a84c;">Prof. Peter N. Devreotes</strong><br>
                    Department of Cell Biology<br>
                    Johns Hopkins School of Medicine<br><br>
                    Research spanning <strong style="color: #c9a84c;">1975 &ndash; 2025</strong> focusing on
                    cell migration, chemotaxis, signal transduction, and the cytoskeleton.</div>
                <div style="color: #c9a84c; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 2px; margin-top: 20px; margin-bottom: 8px;">🔎 Paper Finder</div>
            </div>""")
            paper_input=gr.Textbox(placeholder="Paper # (e.g. 276)",show_label=False)
            paper_lookup_btn=gr.Button("Look Up Paper",size="sm",variant="secondary")
            paper_details=gr.Markdown("Enter a paper number.")

    # EVENT HANDLERS
    def set_lang(lc):
        global current_lang
        current_lang="fr" if lc=="Français" else "en"
        if current_lang=="fr":
            return (gr.update(value="Comment Ras régule-t-il la migration cellulaire ?"),
                    gr.update(value="Quelle est la protéine la plus étudiée ?"),
                    gr.update(value="Quels articles utilisent l'optogénétique ?"))
        return (gr.update(value="How does Ras regulate cell migration?"),
                gr.update(value="What is the most studied protein?"),
                gr.update(value="Which papers use optogenetics?"))
    lang_en.click(fn=lambda: set_lang("English"),outputs=[fup1,fup2,fup3])
    lang_fr.click(fn=lambda: set_lang("Français"),outputs=[fup1,fup2,fup3])

    def respond(message,ch):
        if not message or not message.strip(): return "",ch,gr.update(choices=get_vis_hist(),value=None),gr.update(),gr.update(),gr.update()
        answer,followups=chat_fn(message,ch)
        ch.append({"role":"user","content":message}); ch.append({"role":"assistant","content":answer})
        f1=gr.update(value=followups[0],visible=True) if len(followups)>0 else gr.update(visible=False)
        f2=gr.update(value=followups[1],visible=True) if len(followups)>1 else gr.update(visible=False)
        f3=gr.update(value=followups[2],visible=True) if len(followups)>2 else gr.update(visible=False)
        return "",ch,gr.update(choices=get_vis_hist(),value=None),f1,f2,f3

    def respond_voice(ap,ch):
        if not ap: return "",ch,gr.update(choices=get_vis_hist(),value=None),gr.update(),gr.update(),gr.update(),None
        text,_=transcribe_audio(ap)
        if not text: return "",ch,gr.update(choices=get_vis_hist(),value=None),gr.update(),gr.update(),gr.update(),None
        answer,followups=chat_fn(text,ch)
        ch.append({"role":"user","content":f"🎤 {text}"}); ch.append({"role":"assistant","content":answer})
        f1=gr.update(value=followups[0],visible=True) if len(followups)>0 else gr.update(visible=False)
        f2=gr.update(value=followups[1],visible=True) if len(followups)>1 else gr.update(visible=False)
        f3=gr.update(value=followups[2],visible=True) if len(followups)>2 else gr.update(visible=False)
        return "",ch,gr.update(choices=get_vis_hist(),value=None),f1,f2,f3,None

    msg.submit(respond,[msg,chatbot],[msg,chatbot,history_dropdown,fup1,fup2,fup3])
    send_btn.click(respond,[msg,chatbot],[msg,chatbot,history_dropdown,fup1,fup2,fup3])
    voice_input.stop_recording(respond_voice,[voice_input,chatbot],[msg,chatbot,history_dropdown,fup1,fup2,fup3,voice_input])

    def click_followup(suggestion,h):
        answer,followups=chat_fn(suggestion,h)
        h.append({"role":"user","content":suggestion}); h.append({"role":"assistant","content":answer})
        f1=gr.update(value=followups[0],visible=True) if len(followups)>0 else gr.update(visible=False)
        f2=gr.update(value=followups[1],visible=True) if len(followups)>1 else gr.update(visible=False)
        f3=gr.update(value=followups[2],visible=True) if len(followups)>2 else gr.update(visible=False)
        return "",h,gr.update(choices=get_vis_hist(),value=None),f1,f2,f3

    for btn in [fup1,fup2,fup3]:
        btn.click(click_followup,[btn,chatbot],[msg,chatbot,history_dropdown,fup1,fup2,fup3])

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

    # LOAD DYNAMIC STATS ON STARTUP
    def load_stats():
        c = get_graph_counts()
        return f"📄 **{c['Paper']}** Papers · 🧬 **{c['Protein']}** Proteins\n\n🔬 **{c['Method']}** Methods · 💡 **{c['Concept']}** Concepts"

    demo.load(load_stats, outputs=[stats_display])

demo.launch(server_name="0.0.0.0",server_port=7860)