import json
import time
import logging


class Ingester:
    def __init__(self, api, store, batch_size=4000):
        self.api, self.store, self.batch_size = api, store, batch_size

    def drain(self):
        start = time.monotonic()
        # The kinds share IDs. Merge before ingestion to preserve global ordering.
        discovered = []
        for kind in ("history", "events"):
            after = self.store.get("chunks_after_" + kind, -1)
            discovered += [(row["chunk"], kind) for row in self.api.get("/events/chunks", kind=kind, after=after)]
        count = 0
        for chunk, kind in sorted(set(discovered)):
            row = self.store.db.execute("SELECT done FROM chunks WHERE id=?", (chunk,)).fetchone()
            if row and row[0]:
                continue
            batch = []
            for line in self.api.lines(chunk):
                if not line:
                    continue
                batch.append(json.loads(line))
                if len(batch) >= self.batch_size:
                    count += self.store.ingest(batch, self.store.get("origin"))
                    batch.clear()
                    if count and count % 100000 == 0:
                        logging.getLogger("controller").info(json.dumps({"event": "ingest_checkpoint", "chunk": chunk,
                            "events_this_drain": count, "seconds": round(time.monotonic() - start, 2)}))
            if batch:
                count += self.store.ingest(batch, self.store.get("origin"))
            with self.store.transaction():
                self.store.db.execute("INSERT INTO chunks VALUES (?,?,1) ON CONFLICT(id) DO UPDATE SET done=1", (chunk, kind))
                self.store.set("chunks_after_" + kind, chunk)
        elapsed = time.monotonic() - start
        with self.store.transaction():
            self.store.set("ingest_seconds", self.store.get("ingest_seconds", 0) + elapsed)
        return count, elapsed
