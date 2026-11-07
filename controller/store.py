"""Single-writer durable event ledger and bounded-memory incremental aggregates."""

import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

from .model import dumps, timestamp_minute

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, kind TEXT, done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY, minute INTEGER, workflow TEXT, type TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS events_config_changes ON events(id) WHERE type='config_changed';
CREATE TABLE IF NOT EXISTS leads(
 id TEXT PRIMARY KEY, workflow TEXT, campaign TEXT, person TEXT, features TEXT, concluded INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS leads_workflow ON leads(workflow,concluded);
CREATE TABLE IF NOT EXISTS campaigns(
 id TEXT PRIMARY KEY, workflow TEXT, minute INTEGER, leads INTEGER, version INTEGER);
CREATE TABLE IF NOT EXISTS calls(
 id INTEGER PRIMARY KEY, workflow TEXT, minute INTEGER, lead TEXT, person TEXT, campaign TEXT,
 version INTEGER, cohort TEXT, welcome TEXT, voice TEXT, caller_id TEXT, prompt TEXT,
 connected INTEGER, engaged INTEGER, flagged INTEGER);
CREATE INDEX IF NOT EXISTS calls_window ON calls(workflow,minute);
CREATE INDEX IF NOT EXISTS calls_lead ON calls(lead);
CREATE TABLE IF NOT EXISTS rollups(
 workflow TEXT, bucket INTEGER, cohort TEXT, welcome TEXT, voice TEXT, caller_id TEXT,
 prompt TEXT, version INTEGER, dials INTEGER, connected INTEGER, engaged INTEGER, flagged INTEGER,
 PRIMARY KEY(workflow,bucket,cohort,welcome,voice,caller_id,prompt,version));
CREATE TABLE IF NOT EXISTS configs(
 workflow TEXT, version INTEGER, minute INTEGER, actor TEXT, payload TEXT,
 PRIMARY KEY(workflow,version));
CREATE TABLE IF NOT EXISTS decisions(
 id INTEGER PRIMARY KEY, workflow TEXT, minute INTEGER, kind TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS decisions_lookup ON decisions(workflow,minute);
CREATE TABLE IF NOT EXISTS proposals(
 id TEXT PRIMARY KEY, workflow TEXT, minute INTEGER, kind TEXT, status TEXT,
 base INTEGER, body TEXT, context TEXT, approval TEXT, submitted INTEGER, updated INTEGER);
CREATE INDEX IF NOT EXISTS proposals_workflow ON proposals(workflow,status);
CREATE TABLE IF NOT EXISTS experiments(
 workflow TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS incidents(
 id TEXT PRIMARY KEY, workflow TEXT, kind TEXT, status TEXT, minute INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS paid(
 id TEXT PRIMARY KEY, workflow TEXT, minute INTEGER, kind TEXT, cost INTEGER,
 status TEXT, request TEXT, response TEXT);
"""


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;"
                               "PRAGMA cache_size=-32768; PRAGMA busy_timeout=30000;" + SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, dumps(value)))

    def decision(self, wid, minute, kind, payload):
        self.db.execute("INSERT INTO decisions(workflow,minute,kind,payload) VALUES (?,?,?,?)",
                        (wid, minute, kind, dumps(payload)))

    def save_config(self, wid, current, minute):
        self.db.execute("INSERT OR IGNORE INTO configs VALUES (?,?,?,?,?)",
                        (wid, current["version"], minute, current.get("actor", "observed"), dumps(current)))

    def experiment(self, wid):
        row = self.db.execute("SELECT payload FROM experiments WHERE workflow=?", (wid,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_experiment(self, wid, experiment):
        self.db.execute("INSERT INTO experiments VALUES (?,?) ON CONFLICT(workflow) DO UPDATE SET payload=excluded.payload",
                        (wid, dumps(experiment)))

    def ingest(self, events, origin):
        """Caller presents chunks in global order. API event IDs strictly increase.

        Watermark + materialisations commit atomically. No committed ID can occur
        after an unprocessed earlier chunk. This also tolerates prefix replay.
        """
        with self.transaction():
            watermark = self.get("event_watermark", -1)
            fresh = [e for e in events if e["event_id"] > watermark]
            if not fresh:
                return 0
            if any(a["event_id"] >= b["event_id"] for a, b in zip(fresh, fresh[1:])):
                raise ValueError("events must arrive in strictly increasing ID order")
            ledger, leads, campaigns, calls, concluded = [], [], [], [], []
            rollups = defaultdict(lambda: [0, 0, 0, 0])
            for event in fresh:
                eid, kind = event["event_id"], event["type"]
                minute = timestamp_minute(event["ts"]) - origin
                wid = event.get("workflow_id")
                ledger.append((eid, minute, wid, kind, None if kind in ("call", "lead") else dumps(event)))
                if kind == "lead":
                    leads.append((event["lead_id"], wid, event["campaign_id"], event["person_id"], dumps(event["features"])))
                elif kind == "campaign_created":
                    campaigns.append((event["campaign_id"], wid, minute, event["leads"], event["config_version_at_creation"]))
                elif kind == "call":
                    v = event["variant"]
                    connected, engaged = int(event["connected"]), int(event["engaged"])
                    flagged = int(bool((event.get("qa") or {}).get("hallucination_flag")))
                    cohort = event.get("cohort_id", "default")
                    calls.append((eid, wid, minute, event["lead_id"], event["person_id"], event["campaign_id"],
                                  event["config_version"], cohort, v["welcome_message"], v["voice"], v["caller_id"],
                                  v["prompt_version"], connected, engaged, flagged))
                    key = (wid, minute // 60, cohort, v["welcome_message"], v["voice"], v["caller_id"],
                           v["prompt_version"], event["config_version"])
                    counts = rollups[key]
                    counts[0] += 1
                    counts[1] += connected
                    counts[2] += engaged
                    counts[3] += flagged
                    if engaged:
                        concluded.append((event["lead_id"],))
            self.db.executemany("INSERT INTO events VALUES (?,?,?,?,?)", ledger)
            self.db.executemany("INSERT OR IGNORE INTO leads(id,workflow,campaign,person,features) VALUES (?,?,?,?,?)", leads)
            self.db.executemany("INSERT OR IGNORE INTO campaigns VALUES (?,?,?,?,?)", campaigns)
            self.db.executemany("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", calls)
            self.db.executemany("UPDATE leads SET concluded=1 WHERE id=?", concluded)
            self.db.executemany("""INSERT INTO rollups VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workflow,bucket,cohort,welcome,voice,caller_id,prompt,version)
                DO UPDATE SET dials=dials+excluded.dials,connected=connected+excluded.connected,
                engaged=engaged+excluded.engaged,flagged=flagged+excluded.flagged""",
                                [(*key, *counts) for key, counts in rollups.items()])
            # Repair out-of-order lead arrival without counting its calls a second time.
            self.db.executemany("UPDATE leads SET concluded=1 WHERE id=? AND concluded=0 AND EXISTS "
                                "(SELECT 1 FROM calls WHERE lead=? AND engaged=1)",
                                [(lead[0], lead[0]) for lead in leads])
            self.set("event_watermark", fresh[-1]["event_id"])
            self.set("events_ingested", self.get("events_ingested", 0) + len(fresh))
            return len(fresh)

    def counts(self, wid, start, end, group=None, prompt=None, hours=None):
        allowed = {None, "caller_id", "prompt", "voice", "welcome", "cohort"}
        if group not in allowed:
            raise ValueError("unsupported aggregate dimension")
        select = f"{group} AS arm," if group else "'all' AS arm,"
        sql = f"SELECT {select} SUM(dials) dials,SUM(connected) connected,SUM(engaged) engaged,SUM(flagged) flagged FROM rollups WHERE workflow=? AND bucket>=? AND bucket<?"
        args = [wid, start // 60, end // 60]
        if prompt:
            sql += " AND prompt=?"
            args.append(prompt)
        if hours:
            sql += " AND ((bucket % 24 + 24) % 24) IN (" + ",".join("?" for _ in hours) + ")"
            args.extend(hours)
        if group:
            sql += f" GROUP BY {group}"
        return {r["arm"]: {k: r[k] or 0 for k in ("dials", "connected", "engaged", "flagged")}
                for r in self.db.execute(sql, args)}

    def connected_upper_bounds(self, wid, start, end, dimension, cohort, prompt, minimum_version):
        """Cheap readiness check; partial boundary hours can only overcount.

        Exact call timestamps and person support are still checked before a look.
        Rollups and calls commit together, so an insufficient upper bound safely
        avoids grouping all person-level observations on every hourly cycle.
        """
        if dimension not in ("voice", "welcome_message", "caller_id"):
            raise ValueError("unsupported experiment dimension")
        column = {"welcome_message": "welcome"}.get(dimension, dimension)
        return {row["arm"]: row["connected"] for row in self.db.execute(
            f"SELECT {column} arm,SUM(connected) connected FROM rollups WHERE workflow=? "
            "AND bucket>=? AND bucket<? AND cohort=? AND prompt=? AND version>=? "
            f"GROUP BY {column}", (wid, start // 60, (end + 59) // 60, cohort, prompt, minimum_version))}

    def person_arms(self, wid, start, end, dimension, cohort, prompt, minimum_version):
        if dimension not in ("voice", "welcome_message", "caller_id"):
            raise ValueError("unsupported experiment dimension")
        column = {"welcome_message": "welcome"}.get(dimension, dimension)
        # Observed call version is authoritative for next-campaign-only allocations.
        return self.db.execute(f"""SELECT person,{column} arm,COUNT(*) dials,SUM(connected) connected,
            SUM(engaged) engaged FROM calls WHERE workflow=? AND minute>=? AND minute<?
            AND cohort=? AND prompt=? AND version>=? GROUP BY person,{column}""",
                               (wid, start, end, cohort, prompt, minimum_version)).fetchall()
