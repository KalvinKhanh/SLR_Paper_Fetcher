"""Small SQLite catalog: DOI resolution, file ownership, and download states."""

import json
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from config import settings
from paper_io import public_url, safe_message, valid_pdf_file

STATES = {
    "PENDING", "PROCESSING", "OPEN_ACCESS", "LOGIN_REQUIRED", "AUTHENTICATED",
    "SESSION_EXPIRED", "INSTITUTION_ACCESS", "NO_ACCESS", "MFA_REQUIRED",
    "CAPTCHA_REQUIRED", "INVALID_DOI", "INVALID_PDF", "RESOLUTION_FAILED",
    "DOWNLOADED", "FAILED", "CANCELLED", "PURCHASE_REQUIRED",
}


class PaperStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS papers (
                    doi TEXT PRIMARY KEY, status TEXT NOT NULL, resolved_url TEXT,
                    hostname TEXT, provider TEXT, filename TEXT, file_path TEXT,
                    source TEXT, message TEXT, metadata TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, doi TEXT NOT NULL, status TEXT NOT NULL,
                    message TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_analyses (
                    doi TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'PENDING',
                    provider TEXT, model TEXT, draft TEXT, error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    id TEXT PRIMARY KEY, doi TEXT NOT NULL REFERENCES papers(doi),
                    status TEXT NOT NULL, provider TEXT, model TEXT,
                    prompt_versions TEXT NOT NULL DEFAULT '{}',
                    document_metadata TEXT NOT NULL DEFAULT '{}',
                    validation TEXT NOT NULL DEFAULT '{}',
                    graph_version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS analysis_runs_doi ON analysis_runs(doi, created_at);
                CREATE TABLE IF NOT EXISTS source_sections (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
                    doi TEXT NOT NULL, section_type TEXT NOT NULL, heading TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL, start_offset INTEGER, end_offset INTEGER, page INTEGER,
                    confidence REAL, provenance TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS scientific_propositions (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
                    doi TEXT NOT NULL, section_id TEXT REFERENCES source_sections(id),
                    text TEXT NOT NULL, normalized_text TEXT, proposition_type TEXT NOT NULL,
                    argumentative_roles TEXT NOT NULL DEFAULT '[]', source_quote TEXT,
                    source_page INTEGER, source_offsets TEXT, is_inferred INTEGER NOT NULL DEFAULT 0,
                    confidence REAL, provenance TEXT NOT NULL DEFAULT '{}',
                    review_status TEXT NOT NULL DEFAULT 'pending'
                );
                CREATE INDEX IF NOT EXISTS propositions_doi ON scientific_propositions(doi, run_id);
                CREATE TABLE IF NOT EXISTS argument_relations (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
                    doi TEXT NOT NULL, relation_type TEXT NOT NULL, premise_ids TEXT NOT NULL DEFAULT '[]',
                    conclusion_id TEXT, target_id TEXT, target_kind TEXT, scheme_id TEXT,
                    explanation TEXT, supporting_quote TEXT, confidence REAL,
                    is_inferred INTEGER NOT NULL DEFAULT 0, provenance TEXT NOT NULL DEFAULT '{}',
                    review_status TEXT NOT NULL DEFAULT 'pending'
                );
                CREATE TABLE IF NOT EXISTS aif_nodes (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
                    doi TEXT NOT NULL, node_type TEXT NOT NULL, text TEXT, proposition_id TEXT,
                    relation_id TEXT, metadata TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS aif_edges (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
                    source_node_id TEXT NOT NULL REFERENCES aif_nodes(id) ON DELETE CASCADE,
                    target_node_id TEXT NOT NULL REFERENCES aif_nodes(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS review_decisions (
                    id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
                    item_id TEXT NOT NULL, item_type TEXT NOT NULL, decision TEXT NOT NULL,
                    note TEXT, reviewer TEXT NOT NULL DEFAULT 'local-user', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cross_paper_batches (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, provider TEXT, model TEXT,
                    prompt_version TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS cross_paper_relationships (
                    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES cross_paper_batches(id) ON DELETE CASCADE,
                    source_doi TEXT NOT NULL, target_doi TEXT NOT NULL, source_proposition_id TEXT NOT NULL,
                    target_proposition_id TEXT NOT NULL, relationship TEXT NOT NULL, explanation TEXT NOT NULL,
                    source_quote TEXT NOT NULL, target_quote TEXT NOT NULL, confidence REAL,
                    provenance TEXT NOT NULL DEFAULT '{}', review_status TEXT NOT NULL DEFAULT 'pending', stale INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS cross_paper_batch ON cross_paper_relationships(batch_id, review_status);
                CREATE TABLE IF NOT EXISTS cross_paper_reviews (
                    id INTEGER PRIMARY KEY, relationship_id TEXT NOT NULL REFERENCES cross_paper_relationships(id) ON DELETE CASCADE,
                    decision TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS papers_filename ON papers(filename);
                CREATE INDEX IF NOT EXISTS analyses_status ON paper_analyses(status, updated_at);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(paper_analyses)")}
            if "stage" not in columns:
                db.execute("ALTER TABLE paper_analyses ADD COLUMN stage TEXT")
            run_columns = {row["name"] for row in db.execute("PRAGMA table_info(analysis_runs)")}
            if "graph_version" not in run_columns:
                db.execute("ALTER TABLE analysis_runs ADD COLUMN graph_version INTEGER NOT NULL DEFAULT 1")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, doi):
        with self.connect() as db:
            row = db.execute("SELECT * FROM papers WHERE doi=?", (doi,)).fetchone()
        return dict(row) if row else None

    def update(self, doi, status, **fields):
        if status not in STATES:
            raise ValueError("Invalid paper status.")
        now = datetime.now(timezone.utc).isoformat()
        if "resolved_url" in fields:
            fields["resolved_url"] = public_url(fields["resolved_url"])
        if "message" in fields:
            fields["message"] = safe_message(fields["message"])
        if "metadata" in fields:
            fields["metadata"] = json.dumps(fields["metadata"], ensure_ascii=False)
        allowed = {"resolved_url", "hostname", "provider", "filename", "file_path", "source", "message", "metadata"}
        if set(fields) - allowed:
            raise ValueError("Invalid paper field.")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO papers(doi,status,updated_at) VALUES(?,?,?)", (doi, status, now))
            names = ["status", "updated_at"] + list(fields)
            db.execute("UPDATE papers SET " + ",".join(name + "=?" for name in names) + " WHERE doi=?",
                       [status, now] + list(fields.values()) + [doi])
            db.execute("INSERT INTO events(doi,status,message,created_at) VALUES(?,?,?,?)",
                       (doi, status, fields.get("message", ""), now))

    def downloaded(self, doi):
        record = self.get(doi)
        return record if record and record["status"] == "DOWNLOADED" and valid_pdf_file(record["file_path"] or "") else None

    def file_owner(self, path):
        with self.connect() as db:
            row = db.execute("SELECT doi FROM papers WHERE file_path=? COLLATE NOCASE", (str(Path(path).resolve()),)).fetchone()
        return row["doi"] if row else None

    def list(self, limit=200):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM papers ORDER BY updated_at DESC LIMIT ?", (limit,))]

    def list_analyses(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM paper_analyses ORDER BY updated_at DESC"
            )]

    def get_analysis(self, doi):
        with self.connect() as db:
            row = db.execute("SELECT * FROM paper_analyses WHERE doi=?", (doi,)).fetchone()
        return dict(row) if row else None

    def save_analysis(self, doi, status, *, provider=None, model=None, draft=None, error=None, stage=None):
        now = datetime.now(timezone.utc).isoformat()
        serialized = json.dumps(draft, ensure_ascii=False) if draft is not None else None
        with self.connect() as db:
            db.execute("""INSERT INTO paper_analyses(doi,status,provider,model,draft,error,created_at,updated_at,stage)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(doi) DO UPDATE SET
                status=excluded.status, provider=excluded.provider, model=excluded.model,
                draft=COALESCE(excluded.draft,paper_analyses.draft), error=excluded.error,
                stage=excluded.stage, updated_at=excluded.updated_at""",
                (doi, status, provider, model, serialized, error, now, now, stage))

    def try_queue_analysis(self, doi, provider, *, allow_done=False):
        now = datetime.now(timezone.utc).isoformat()
        done_clause = "" if allow_done else "AND paper_analyses.status != 'DONE'"
        with self.connect() as db:
            cursor = db.execute(f"""INSERT INTO paper_analyses(doi,status,provider,error,created_at,updated_at)
                VALUES(?, 'PENDING', ?, NULL, ?, ?)
                ON CONFLICT(doi) DO UPDATE SET status='PENDING', provider=excluded.provider,
                    error=NULL, updated_at=excluded.updated_at
                WHERE paper_analyses.status NOT IN ('PENDING','PROCESSING') {done_clause}""",
                (doi, provider, now, now))
            return cursor.rowcount == 1

    def update_analysis_stage(self, doi, stage):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("UPDATE paper_analyses SET stage=?,updated_at=? WHERE doi=? AND status='PROCESSING'",
                       (stage, now, doi))

    def save_argumentation(self, doi, run):
        """Persist one complete, versioned analysis result atomically."""
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("UPDATE cross_paper_relationships SET stale=1 WHERE source_doi=? OR target_doi=?", (doi, doi))
            db.execute("""INSERT INTO analysis_runs
                (id,doi,status,provider,model,prompt_versions,document_metadata,validation,graph_version,created_at,completed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (run["id"], doi, run.get("status", "DONE"), run.get("provider"), run.get("model"),
                 json.dumps(run.get("prompt_versions", {}), ensure_ascii=False),
                 json.dumps(run.get("document", {}).get("metadata", {}), ensure_ascii=False),
                 json.dumps(run.get("validation", {}), ensure_ascii=False), 1, now, now))
            for section in run.get("document", {}).get("sections", []):
                db.execute("""INSERT INTO source_sections
                    (id,run_id,doi,section_type,heading,text,start_offset,end_offset,page,confidence,provenance)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (section["id"], run["id"], doi, section.get("section_type", "other"),
                     section.get("heading", ""), section.get("text", ""), section.get("start_offset"),
                     section.get("end_offset"), section.get("page"), section.get("confidence"),
                     json.dumps(section.get("provenance", {}), ensure_ascii=False)))
            for item in run.get("propositions", []):
                db.execute("""INSERT INTO scientific_propositions
                    (id,run_id,doi,section_id,text,normalized_text,proposition_type,argumentative_roles,
                     source_quote,source_page,source_offsets,is_inferred,confidence,provenance,review_status)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (item["id"], run["id"], doi, item.get("section_id"), item["text"],
                     item.get("normalized_text"), item.get("proposition_type", "other"),
                     json.dumps(item.get("argumentative_roles", []), ensure_ascii=False),
                     item.get("source_quote"), item.get("source_page"),
                     json.dumps(item.get("source_offsets"), ensure_ascii=False) if item.get("source_offsets") else None,
                     int(bool(item.get("is_inferred"))), item.get("confidence"),
                     json.dumps(item.get("provenance", {}), ensure_ascii=False), item.get("review_status", "pending")))
            for relation in run.get("relations", []):
                db.execute("""INSERT INTO argument_relations
                    (id,run_id,doi,relation_type,premise_ids,conclusion_id,target_id,target_kind,scheme_id,
                     explanation,supporting_quote,confidence,is_inferred,provenance,review_status)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (relation["id"], run["id"], doi, relation.get("relation_type", "supports"),
                     json.dumps(relation.get("premise_ids", [])), relation.get("conclusion_id"),
                     relation.get("target_id"), relation.get("target_kind"), relation.get("scheme_id"),
                     relation.get("explanation"), relation.get("supporting_quote"), relation.get("confidence"),
                     int(bool(relation.get("is_inferred"))), json.dumps(relation.get("provenance", {}), ensure_ascii=False),
                     relation.get("review_status", "pending")))
            for node in run.get("graph", {}).get("nodes", []):
                db.execute("""INSERT INTO aif_nodes(id,run_id,doi,node_type,text,proposition_id,relation_id,metadata)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (node["id"], run["id"], doi, node["node_type"], node.get("text"),
                     node.get("proposition_id"), node.get("relation_id"),
                     json.dumps(node.get("metadata", {}), ensure_ascii=False)))
            for edge in run.get("graph", {}).get("edges", []):
                db.execute("INSERT INTO aif_edges(id,run_id,source_node_id,target_node_id) VALUES(?,?,?,?)",
                           (edge["id"], run["id"], edge["source_node_id"], edge["target_node_id"]))

    def get_argumentation(self, doi):
        with self.connect() as db:
            run = db.execute("SELECT * FROM analysis_runs WHERE doi=? ORDER BY created_at DESC LIMIT 1", (doi,)).fetchone()
            if not run:
                return None
            run = dict(run)
            run_id = run["id"]
            sections = [dict(row) for row in db.execute("SELECT * FROM source_sections WHERE run_id=? ORDER BY start_offset", (run_id,))]
            propositions = [dict(row) for row in db.execute("SELECT * FROM scientific_propositions WHERE run_id=? ORDER BY rowid", (run_id,))]
            relations = [dict(row) for row in db.execute("SELECT * FROM argument_relations WHERE run_id=? ORDER BY rowid", (run_id,))]
            nodes = [dict(row) for row in db.execute("SELECT * FROM aif_nodes WHERE run_id=? ORDER BY rowid", (run_id,))]
            edges = [dict(row) for row in db.execute("SELECT * FROM aif_edges WHERE run_id=? ORDER BY rowid", (run_id,))]
            reviews = [dict(row) for row in db.execute("SELECT * FROM review_decisions WHERE run_id=? ORDER BY created_at", (run_id,))]
        for row in (sections + nodes):
            key = "provenance" if "provenance" in row else "metadata"
            row[key] = json.loads(row.get(key) or "{}")
        for item in propositions:
            item["argumentative_roles"] = json.loads(item.get("argumentative_roles") or "[]")
            item["source_offsets"] = json.loads(item["source_offsets"]) if item.get("source_offsets") else None
            item["provenance"] = json.loads(item.get("provenance") or "{}")
            item["is_inferred"] = bool(item["is_inferred"])
        for item in relations:
            item["premise_ids"] = json.loads(item.get("premise_ids") or "[]")
            item["provenance"] = json.loads(item.get("provenance") or "{}")
            item["is_inferred"] = bool(item["is_inferred"])
        run["prompt_versions"] = json.loads(run.get("prompt_versions") or "{}")
        run["document_metadata"] = json.loads(run.get("document_metadata") or "{}")
        run["validation"] = json.loads(run.get("validation") or "{}")
        run["sections"] = sections
        run["propositions"] = propositions
        run["relations"] = relations
        proposition_by_id = {item["id"]: item for item in propositions}
        relation_by_id = {item["id"]: item for item in relations}
        for node in nodes:
            record = proposition_by_id.get(node.get("proposition_id")) or relation_by_id.get(node.get("relation_id"))
            if record:
                node["metadata"].update({"review_status": record.get("review_status", "pending"),
                                         "is_inferred": bool(record.get("is_inferred"))})
        try:
            from ai_analysis import build_aif_graph
            graph, validation = build_aif_graph(propositions, relations)
            nodes, edges = graph["nodes"], graph["edges"]
            run["validation"] = validation
        except (ImportError, KeyError, TypeError):
            pass
        run["graph"] = {"nodes": nodes, "edges": edges, "version": run.get("graph_version", 1)}
        run["reviews"] = reviews
        return run

    def review_analysis_item(self, doi, item_id, item_type, decision, note="", reviewer="local-user"):
        if item_type not in {"proposition", "relation"} or decision not in {"approved", "rejected", "needs_revision"}:
            raise ValueError("Invalid review decision.")
        now = datetime.now(timezone.utc).isoformat()
        table = "scientific_propositions" if item_type == "proposition" else "argument_relations"
        with self.connect() as db:
            run = db.execute("SELECT id FROM analysis_runs WHERE doi=? ORDER BY created_at DESC LIMIT 1", (doi,)).fetchone()
            if not run:
                return False
            cursor = db.execute(f"UPDATE {table} SET review_status=? WHERE id=? AND run_id=?", (decision, item_id, run["id"]))
            if cursor.rowcount != 1:
                return False
            db.execute("INSERT INTO review_decisions(run_id,item_id,item_type,decision,note,reviewer,created_at) VALUES(?,?,?,?,?,?,?)",
                       (run["id"], item_id, item_type, decision, note[:2000], reviewer[:200], now))
            db.execute("UPDATE analysis_runs SET graph_version=graph_version+1 WHERE id=?", (run["id"],))
            db.execute("UPDATE cross_paper_relationships SET stale=1 WHERE source_doi=? OR target_doi=?", (doi, doi))
        return True

    def edit_analysis_item(self, doi, item_id, item_type, changes, note=""):
        allowed_types = {"research_gap", "research_question", "objective", "hypothesis", "finding", "conclusion",
                         "background", "assumption", "implication", "limitation", "future_work", "other"}
        allowed_roles = {"claim", "evidence", "warrant", "backing", "qualifier", "rebuttal"}
        if not isinstance(changes, dict) or item_type not in {"proposition", "relation"}:
            raise ValueError("Invalid edit payload.")
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            run = db.execute("SELECT id FROM analysis_runs WHERE doi=? ORDER BY created_at DESC LIMIT 1", (doi,)).fetchone()
            if not run:
                return False
            run_id = run["id"]
            if item_type == "proposition":
                if set(changes) - {"text", "proposition_type", "argumentative_roles"}:
                    raise ValueError("Unsupported proposition field.")
                if "proposition_type" in changes and changes["proposition_type"] not in allowed_types:
                    raise ValueError("Invalid proposition type.")
                if "argumentative_roles" in changes and (not isinstance(changes["argumentative_roles"], list) or
                    not set(changes["argumentative_roles"]) <= allowed_roles):
                    raise ValueError("Invalid argument role.")
                columns = {"text": "text", "proposition_type": "proposition_type"}
                assignments, values = [], []
                for key in ("text", "proposition_type"):
                    if key in changes:
                        assignments.append(f"{columns[key]}=?"); values.append(str(changes[key])[:12000])
                if "argumentative_roles" in changes:
                    assignments.append("argumentative_roles=?"); values.append(json.dumps(changes["argumentative_roles"]))
                assignments.append("review_status='needs_revision'")
                cursor = db.execute(f"UPDATE scientific_propositions SET {','.join(assignments)} WHERE id=? AND run_id=?", values + [item_id, run_id])
            else:
                if set(changes) - {"relation_type", "premise_ids", "conclusion_id", "target_id", "target_kind", "explanation"}:
                    raise ValueError("Unsupported relation field.")
                relation_types = {"supports", "undermine", "undercut", "rebut", "qualifies", "extends"}
                if "relation_type" in changes and changes["relation_type"] not in relation_types:
                    raise ValueError("Invalid relation type.")
                proposition_ids = {row["id"] for row in db.execute("SELECT id FROM scientific_propositions WHERE run_id=?", (run_id,))}
                if "premise_ids" in changes and (not isinstance(changes["premise_ids"], list) or not set(changes["premise_ids"]) <= proposition_ids):
                    raise ValueError("One or more premises do not exist.")
                if changes.get("conclusion_id") and changes["conclusion_id"] not in proposition_ids:
                    raise ValueError("Conclusion does not exist.")
                if changes.get("target_id") and changes["target_id"] not in proposition_ids:
                    raise ValueError("Attack target does not exist.")
                current = db.execute("SELECT relation_type,premise_ids,conclusion_id,target_id,target_kind FROM argument_relations WHERE id=? AND run_id=?",
                                     (item_id, run_id)).fetchone()
                if current:
                    next_type = changes.get("relation_type", current["relation_type"])
                    next_premises = changes.get("premise_ids", json.loads(current["premise_ids"] or "[]"))
                    next_conclusion = changes.get("conclusion_id", current["conclusion_id"])
                    next_target = changes.get("target_id", current["target_id"])
                    next_target_kind = changes.get("target_kind", current["target_kind"])
                    if not next_premises:
                        raise ValueError("A relation requires at least one premise.")
                    if next_type == "supports" and not next_conclusion:
                        raise ValueError("A support relation requires a conclusion.")
                    if next_type in {"undermine", "rebut"} and (not next_target or next_target_kind != "proposition"):
                        raise ValueError("A conflict relation requires a proposition target.")
                    if next_type == "undercut" and (not next_target or next_target_kind != "inference"):
                        raise ValueError("An undercut relation requires an inference target.")
                assignments, values = [], []
                for key in ("relation_type", "conclusion_id", "target_id", "target_kind", "explanation"):
                    if key in changes:
                        assignments.append(f"{key}=?"); values.append(str(changes[key])[:4000] if changes[key] is not None else None)
                if "premise_ids" in changes:
                    assignments.append("premise_ids=?"); values.append(json.dumps(changes["premise_ids"]))
                assignments.append("review_status='needs_revision'")
                cursor = db.execute(f"UPDATE argument_relations SET {','.join(assignments)} WHERE id=? AND run_id=?", values + [item_id, run_id])
            if cursor.rowcount != 1:
                return False
            db.execute("INSERT INTO review_decisions(run_id,item_id,item_type,decision,note,reviewer,created_at) VALUES(?,?,?,?,?,?,?)",
                       (run_id, item_id, item_type, "edited", note[:2000], "local-user", now))
            db.execute("UPDATE analysis_runs SET graph_version=graph_version+1 WHERE id=?", (run_id,))
            db.execute("UPDATE cross_paper_relationships SET stale=1 WHERE source_doi=? OR target_doi=?", (doi, doi))
        return True

    def approved_propositions_for_synthesis(self, limit=120):
        with self.connect() as db:
            rows = db.execute("""SELECT p.*,r.provider,r.model FROM scientific_propositions p
                JOIN analysis_runs r ON r.id=p.run_id
                WHERE p.review_status='approved' AND r.id=(SELECT r2.id FROM analysis_runs r2
                    WHERE r2.doi=p.doi ORDER BY r2.created_at DESC LIMIT 1)
                ORDER BY p.doi,p.rowid LIMIT ?""", (limit,)).fetchall()
        values = [dict(row) for row in rows]
        for item in values:
            item["argumentative_roles"] = json.loads(item.get("argumentative_roles") or "[]")
            item["provenance"] = json.loads(item.get("provenance") or "{}")
            item["source_offsets"] = json.loads(item["source_offsets"]) if item.get("source_offsets") else None
            item["is_inferred"] = bool(item["is_inferred"])
        return values

    def approved_argumentation_graphs(self):
        with self.connect() as db:
            dois = [row["doi"] for row in db.execute("""SELECT r.doi FROM analysis_runs r
                WHERE r.id=(SELECT r2.id FROM analysis_runs r2 WHERE r2.doi=r.doi ORDER BY r2.created_at DESC LIMIT 1)
                ORDER BY r.doi""")]
        graphs = []
        for doi in dois:
            run = self.get_argumentation(doi)
            if not run:
                continue
            propositions = [item for item in run["propositions"] if item["review_status"] == "approved"]
            relations = [item for item in run["relations"] if item["review_status"] == "approved"]
            from ai_analysis import build_aif_graph
            graph, validation = build_aif_graph(propositions, relations)
            graphs.append({"doi": doi, "run_id": run["id"], "propositions": propositions,
                           "relations": relations, "graph": graph, "validation": validation})
        return graphs

    def create_cross_paper_batch(self, batch_id, provider, prompt_version):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("INSERT INTO cross_paper_batches(id,status,provider,prompt_version,created_at) VALUES(?,'PROCESSING',?,?,?)",
                       (batch_id, provider, prompt_version, now))

    def complete_cross_paper_batch(self, batch_id, model, relationships):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            for item in relationships:
                db.execute("""INSERT INTO cross_paper_relationships
                    (id,batch_id,source_doi,target_doi,source_proposition_id,target_proposition_id,relationship,
                     explanation,source_quote,target_quote,confidence,provenance,review_status)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (item["id"], batch_id, item["source_doi"], item["target_doi"], item["source_proposition_id"],
                     item["target_proposition_id"], item["relationship"], item["explanation"],
                     item["source_quote"], item["target_quote"], item.get("confidence"),
                     json.dumps(item["provenance"], ensure_ascii=False), "pending"))
            db.execute("UPDATE cross_paper_batches SET status='DONE',model=?,completed_at=? WHERE id=?",
                       (model, now, batch_id))

    def fail_cross_paper_batch(self, batch_id, error):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("UPDATE cross_paper_batches SET status='FAILED',error=?,completed_at=? WHERE id=?",
                       (error[:500], now, batch_id))

    def list_cross_paper_relationships(self):
        with self.connect() as db:
            batch = db.execute("SELECT * FROM cross_paper_batches ORDER BY created_at DESC LIMIT 1").fetchone()
            if not batch:
                return {"batch": None, "relationships": []}
            batch = dict(batch)
            rows = [dict(row) for row in db.execute("SELECT * FROM cross_paper_relationships WHERE batch_id=? ORDER BY rowid", (batch["id"],))]
            for row in rows:
                row["provenance"] = json.loads(row.get("provenance") or "{}")
                row["stale"] = bool(row["stale"])
                reviews = db.execute("SELECT decision,note,created_at FROM cross_paper_reviews WHERE relationship_id=? ORDER BY created_at", (row["id"],)).fetchall()
                row["reviews"] = [dict(review) for review in reviews]
        return {"batch": batch, "relationships": rows}

    def review_cross_paper_relationship(self, relationship_id, decision, note=""):
        if decision not in {"approved", "rejected", "needs_revision"}:
            raise ValueError("Invalid review decision.")
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            cursor = db.execute("UPDATE cross_paper_relationships SET review_status=? WHERE id=?", (decision, relationship_id))
            if cursor.rowcount != 1:
                return False
            db.execute("INSERT INTO cross_paper_reviews(relationship_id,decision,note,created_at) VALUES(?,?,?,?)",
                       (relationship_id, decision, note[:2000], now))
        return True


@lru_cache(maxsize=4)
def _store(path):
    return PaperStore(path)


_STORE_LOCK = Lock()


def get_store():
    # lru_cache permits simultaneous calls on a cache miss. Serialize catalog
    # initialization so scan workers do not race PRAGMA/schema creation.
    with _STORE_LOCK:
        return _store(str(settings.DB_PATH.resolve()))
