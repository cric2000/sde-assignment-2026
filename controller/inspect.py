"""Read-only supervisor and interview queries against the controller's own store."""

import argparse
import json
import sqlite3
from pathlib import Path

from .model import Cohorts, timestamp_minute
from .statistics import rate


class Reader:
    def __init__(self, path):
        self.db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row

    def close(self):
        self.db.close()

    def meta(self):
        return {r["key"]: json.loads(r["value"]) for r in self.db.execute("SELECT * FROM meta")}

    def overview(self):
        meta = self.meta()
        workflows = []
        for key, definition in sorted(meta.items()):
            if not key.startswith("workflow:"):
                continue
            wid = definition["workflow_id"]
            row = self.db.execute("SELECT SUM(dials) dials,SUM(connected) connected,SUM(engaged) engaged,SUM(flagged) flagged "
                                  "FROM rollups WHERE workflow=? AND bucket>=0", (wid,)).fetchone()
            counts = {k: row[k] or 0 for k in row.keys()}
            leads = self.db.execute("SELECT COUNT(*) uploaded,SUM(l.concluded) concluded FROM leads l JOIN campaigns c "
                                    "ON l.campaign=c.id WHERE l.workflow=? AND c.minute>=0", (wid,)).fetchone()
            config = self.db.execute("SELECT payload FROM configs WHERE workflow=? ORDER BY version DESC LIMIT 1", (wid,)).fetchone()
            experiment = self.db.execute("SELECT payload FROM experiments WHERE workflow=?", (wid,)).fetchone()
            workflows.append({"workflow_id": wid, "tenant_id": definition["tenant_id"], "objective": definition["objective"],
                              "counts": counts, "pickup_rate": rate(counts, "pickup_rate"),
                              "engagement_rate": rate(counts, "engagement_rate"),
                              "leads_uploaded": leads["uploaded"], "leads_concluded": leads["concluded"] or 0,
                              "business_outcome": (leads["concluded"] or 0) / leads["uploaded"] if leads["uploaded"] else None,
                              "cohorts": Cohorts(json.loads(config[0])["config"]).describe() if config else [],
                              "experiment": json.loads(experiment[0]) if experiment else None})
        return {"metrics": {k: v for k, v in meta.items() if not k.startswith(("workflow:", "experiment_attempt:", "hallucination_hits:", "caller_hits:"))},
                "workflows": workflows,
                "proposals": [dict(r) for r in self.db.execute("SELECT id,workflow,minute,kind,status,approval,updated FROM proposals ORDER BY minute")],
                "incidents": [dict(r) | {"payload": json.loads(r["payload"])} for r in self.db.execute("SELECT * FROM incidents ORDER BY minute")],
                "paid": [dict(r) for r in self.db.execute("SELECT id,workflow,minute,kind,cost,status FROM paid ORDER BY minute")]}

    def why(self, wid, at=None):
        meta = self.meta()
        minute = timestamp_minute(at) - meta["origin"] if at else meta.get("sim_minute", 0)
        change = self.db.execute("SELECT minute,payload FROM events WHERE workflow=? AND type='config_changed' "
                                  "AND minute<=? ORDER BY id DESC LIMIT 1", (wid, minute)).fetchone()
        event = json.loads(change["payload"]) if change else None
        if event and event["actor"] == "supervisor":
            snapshot = self.db.execute("SELECT payload FROM configs WHERE workflow=? AND version=?", (wid, event["version"])).fetchone()
            return {"workflow": wid, "at_minute": minute, "change": event,
                    "authority": "client supervisor", "rationale": "not provided by the HTTP contract",
                    "observed_config": json.loads(snapshot[0]) if snapshot else None,
                    "timeline": self.timeline(wid, change["minute"], min(meta["sim_minute"], change["minute"] + 1440))}
        proposal = None
        if event and event.get("proposal_id"):
            proposal = self.db.execute("SELECT * FROM proposals WHERE id=?", (event["proposal_id"],)).fetchone()
        if proposal is None:
            proposal = self.db.execute("SELECT * FROM proposals WHERE workflow=? AND status='applied' AND updated<=? "
                                       "ORDER BY updated DESC LIMIT 1", (wid, minute)).fetchone()
        if not proposal:
            return {"workflow": wid, "at_minute": minute, "change": None,
                    "decisions": self.timeline(wid, max(0, minute - 1440), minute)}
        result = dict(proposal)
        result["body"], result["context"] = json.loads(result["body"]), json.loads(result["context"])
        result["authority"] = {"approval_id": result["approval"],
                               "approver": "simulated client approver; named identity not supplied" if result["approval"] else "workflow auto-apply policy"}
        result["timeline"] = self.timeline(wid, proposal["minute"], min(meta["sim_minute"], proposal["updated"] + 1440))
        for label, lo, hi in (("before", proposal["updated"] - 1440, proposal["updated"]),
                              ("after", proposal["updated"], min(meta["sim_minute"], proposal["updated"] + 1440))):
            row = self.db.execute("SELECT COUNT(*) dials,SUM(connected) connected,SUM(engaged) engaged FROM calls "
                                  "WHERE workflow=? AND minute>=? AND minute<?", (wid, lo, hi)).fetchone()
            counts = {k: row[k] or 0 for k in row.keys()}
            result[label] = counts | {"pickup_rate": rate(counts, "pickup_rate"), "engagement_rate": rate(counts, "engagement_rate"),
                                       "from_minute": lo, "to_minute": hi, "causal_claim": False}
        return result

    def timeline(self, wid, start=-100000, end=10**9):
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in self.db.execute(
            "SELECT * FROM decisions WHERE workflow=? AND minute>=? AND minute<=? ORDER BY minute,id", (wid, start, end))]

    def exposure(self, lead):
        row = self.db.execute("SELECT * FROM leads WHERE id=?", (lead,)).fetchone()
        calls = []
        for record in self.db.execute("SELECT * FROM calls WHERE lead=? ORDER BY minute,id", (lead,)):
            call = dict(record)
            snapshot = self.db.execute("SELECT payload FROM configs WHERE workflow=? AND version=?", (call["workflow"], call["version"])).fetchone()
            call["agent_config_snapshot"] = json.loads(snapshot[0]) if snapshot else None
            prompt_change = self.db.execute("SELECT payload FROM events WHERE workflow=? AND type='config_changed' AND minute<=? "
                "AND EXISTS (SELECT 1 FROM json_each(json_extract(payload,'$.changed_dims')) WHERE value='prompt_version') "
                "ORDER BY id DESC LIMIT 1", (call["workflow"], call["minute"])).fetchone()
            call["prompt_change_authority"] = json.loads(prompt_change[0]) if prompt_change else "business default"
            calls.append(call)
        return {"lead": dict(row) if row else None, "calls": calls,
            "note": "actual call prompt is recorded separately from campaign-locked config version"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("overview")
    why = sub.add_parser("why")
    why.add_argument("--workflow", required=True)
    why.add_argument("--at")
    timeline = sub.add_parser("timeline")
    timeline.add_argument("--workflow", required=True)
    exposure = sub.add_parser("exposure")
    exposure.add_argument("--lead", required=True)
    args = parser.parse_args()
    reader = Reader(args.db)
    try:
        result = reader.overview() if args.command == "overview" else reader.why(args.workflow, args.at) if args.command == "why" else reader.timeline(args.workflow) if args.command == "timeline" else reader.exposure(args.lead)
        print(json.dumps(result, indent=2))
    finally:
        reader.close()


if __name__ == "__main__":
    main()
