# ============================================================
# Graph Reload Script — Devreotes Lab GraphRAG
# Sylvester Vhashe & Tadiwanashe C. Dzimbanete
# DAV 6500 Capstone | Yeshiva University | 2026
# ============================================================
# This script rebuilds the knowledge graph from the Cypher
# backup file. We wrote it after our Neo4j Aura Free instance
# expired due to inactivity — having a reload script meant
# we could get everything back up in under 5 minutes.
#
# Usage:
#   1. pip install neo4j
#   2. Place load_graph.cypher in the same directory
#   3. Set the NEO4J credentials below
#   4. Run: python reload_graph.py
# ============================================================

import os
from neo4j import GraphDatabase

# Set these to your Neo4j Aura credentials
NEO4J_URI = os.environ.get("NEO4J_URI", "neo4j+s://YOUR_INSTANCE.databases.neo4j.io")
NEO4J_USER = os.environ.get("NEO4J_USER", "YOUR_USERNAME")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "YOUR_PASSWORD")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# Step 1: Load the Cypher file
# We generated this file from our entity extraction pipeline.
# It contains 3,887 MERGE statements — one for each paper node,
# protein, method, concept, organism, and their relationships.
print("Loading graph from load_graph.cypher...")
with open("load_graph.cypher", "r") as f:
    statements = [line.strip() for line in f if line.strip()]

print(f"Found {len(statements)} statements")

errors = []
with driver.session() as session:
    for i, stmt in enumerate(statements):
        try:
            session.run(stmt)
        except Exception as e:
            errors.append(f"Line {i+1}: {str(e)[:100]}")
        if (i+1) % 500 == 0:
            print(f"  Loaded {i+1}/{len(statements)}...")

print(f"Loaded {len(statements)} statements with {len(errors)} errors")
if errors:
    for e in errors[:5]:
        print(f"  Error: {e}")

# Step 2: Remove duplicate papers
# During the initial build, we found 3 papers that existed
# twice under different numbers (same title, same year).
# We verified them manually before deleting.
print("\nRemoving known duplicate papers...")
with driver.session() as session:
    for num in ['271', '090', '034']:
        session.run("MATCH (p:Paper {number: $n}) DETACH DELETE p", n=num)
        print(f"  Removed Paper #{num}")

# Step 3: Create Gene nodes
# The graph schema originally had only Protein nodes. Our team
# added Gene nodes that mirror the Protein nodes — same names,
# same paper connections. In biology, most proteins in this
# research are named after their genes (PTEN the gene encodes
# PTEN the protein), so this gives us both entity types.
print("\nCreating Gene nodes from existing Proteins...")
with driver.session() as session:
    result = session.run(
        "MATCH (p:Paper)-[r:MENTIONS_PROTEIN]->(pr:Protein) "
        "RETURN p.number AS paper_num, pr.name AS protein, r.context AS context"
    )
    records = list(result)
    print(f"  Found {len(records)} protein-paper connections")

    count = 0
    for r in records:
        try:
            session.run(
                "MERGE (g:Gene {name: $name}) "
                "WITH g MATCH (p:Paper {number: $num}) "
                "MERGE (p)-[:MENTIONS_GENE {context: $ctx}]->(g)",
                name=r['protein'], num=r['paper_num'],
                ctx=r['context'] or ''
            )
            count += 1
        except Exception as e:
            errors.append(str(e)[:100])
        if count % 200 == 0 and count > 0:
            print(f"  Created {count} gene connections...")

    print(f"  Created {count} gene connections total")

# Step 4: Verify the final state
print("\nFinal graph state:")
with driver.session() as session:
    for label in ['Paper', 'Protein', 'Gene', 'Method', 'Concept', 'Organism']:
        result = session.run(f"MATCH (n:{label}) RETURN count(n) AS count")
        print(f"  {label}: {result.single()['count']}")
    result = session.run(
        "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count ORDER BY count DESC"
    )
    print("Relationships:")
    for r in result:
        print(f"  {r['type']}: {r['count']}")

driver.close()
print("\nGraph rebuilt successfully.")
