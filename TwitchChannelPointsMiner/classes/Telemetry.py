import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)


class Telemetry:
    """SQLite-backed telemetry store for the Twitch Channel Points Miner.

    Provides structured storage for prediction results, point gains,
    streamer sessions, and generic events — replacing the log-only approach
    so that stats like win-rate can be computed reliably.
    """

    def __init__(self, db_path=None):
        if db_path is None:
            from TwitchChannelPointsMiner.classes.Settings import Settings
            db_path = os.path.join(Settings.analytics_path, "telemetry.db")
        self.db_path = db_path
        Path(os.path.dirname(db_path)).mkdir(parents=True, exist_ok=True)
        self._write_lock = Lock()
        self._init_db()

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    NOT NULL,
                    event_type  TEXT    NOT NULL,
                    streamer    TEXT,
                    data        TEXT,
                    source      TEXT    DEFAULT 'runtime'
                );

                CREATE TABLE IF NOT EXISTS predictions (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id       TEXT,
                    timestamp      TEXT    NOT NULL,
                    streamer       TEXT    NOT NULL,
                    title          TEXT,
                    choice_index   INTEGER,
                    choice_title   TEXT,
                    choice_color   TEXT,
                    amount_placed  INTEGER DEFAULT 0,
                    result         TEXT,
                    points_gained  INTEGER DEFAULT 0,
                    confidence     REAL,
                    strategy_used  TEXT,
                    source         TEXT    DEFAULT 'runtime',
                    UNIQUE(event_id)
                );

                CREATE TABLE IF NOT EXISTS dry_run_results (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    prediction_id  INTEGER,
                    timestamp      TEXT    NOT NULL,
                    streamer       TEXT    NOT NULL,
                    event_title    TEXT,
                    strategy       TEXT    NOT NULL,
                    choice_index   INTEGER,
                    outcome_title  TEXT,
                    outcome_color  TEXT,
                    amount         INTEGER DEFAULT 0,
                    result         TEXT,
                    points_gained  INTEGER DEFAULT 0,
                    is_active      INTEGER DEFAULT 0,
                    FOREIGN KEY (prediction_id) REFERENCES predictions(id)
                );

                CREATE TABLE IF NOT EXISTS points_history (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     TEXT    NOT NULL,
                    streamer      TEXT    NOT NULL,
                    amount        INTEGER NOT NULL,
                    reason        TEXT,
                    balance_after INTEGER,
                    source        TEXT    DEFAULT 'runtime'
                );

                CREATE TABLE IF NOT EXISTS streamer_sessions (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    streamer         TEXT NOT NULL,
                    online_at        TEXT,
                    offline_at       TEXT,
                    duration_seconds INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_events_type     ON events(event_type);
                CREATE INDEX IF NOT EXISTS idx_events_streamer  ON events(streamer);
                CREATE INDEX IF NOT EXISTS idx_events_ts        ON events(timestamp);
                CREATE INDEX IF NOT EXISTS idx_pred_streamer    ON predictions(streamer);
                CREATE INDEX IF NOT EXISTS idx_pred_result      ON predictions(result);
                CREATE INDEX IF NOT EXISTS idx_pred_ts          ON predictions(timestamp);
                CREATE INDEX IF NOT EXISTS idx_pts_streamer     ON points_history(streamer);
                CREATE INDEX IF NOT EXISTS idx_pts_ts           ON points_history(timestamp);
                CREATE INDEX IF NOT EXISTS idx_sess_streamer    ON streamer_sessions(streamer);
                CREATE TABLE IF NOT EXISTS strategy_switches (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     TEXT    NOT NULL,
                    streamer      TEXT,
                    old_strategy  TEXT,
                    new_strategy  TEXT    NOT NULL,
                    reason        TEXT,
                    source        TEXT    DEFAULT 'manual'
                );

                CREATE INDEX IF NOT EXISTS idx_dr_streamer      ON dry_run_results(streamer);
                CREATE INDEX IF NOT EXISTS idx_dr_strategy      ON dry_run_results(strategy);
                CREATE INDEX IF NOT EXISTS idx_dr_ts            ON dry_run_results(timestamp);
                CREATE INDEX IF NOT EXISTS idx_dr_pred          ON dry_run_results(prediction_id);
                CREATE INDEX IF NOT EXISTS idx_sw_streamer      ON strategy_switches(streamer);
                CREATE INDEX IF NOT EXISTS idx_sw_ts            ON strategy_switches(timestamp);
            """)
            conn.commit()

            # Migrations for existing databases
            try:
                conn.execute("SELECT strategy_used FROM predictions LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE predictions ADD COLUMN strategy_used TEXT")
                conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Write methods (all protected by _write_lock)
    # ------------------------------------------------------------------

    def record_event(self, event_type, streamer=None, data=None,
                     timestamp=None, source="runtime"):
        if timestamp is None:
            timestamp = datetime.now().isoformat()
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO events (timestamp, event_type, streamer, data, source) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (timestamp, event_type, streamer,
                     json.dumps(data) if data else None, source),
                )
                conn.commit()
            finally:
                conn.close()

    def record_prediction(self, timestamp, streamer, title=None,
                          event_id=None, choice_index=None,
                          choice_title=None, choice_color=None,
                          amount_placed=0, result=None, points_gained=0,
                          confidence=None, strategy_used=None, source="runtime"):
        with self._write_lock:
            conn = self._get_conn()
            try:
                if event_id:
                    conn.execute(
                        """INSERT INTO predictions
                               (event_id, timestamp, streamer, title, choice_index,
                                choice_title, choice_color, amount_placed, result,
                                points_gained, confidence, strategy_used, source)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(event_id) DO UPDATE SET
                               result        = excluded.result,
                               points_gained = excluded.points_gained,
                               strategy_used = COALESCE(excluded.strategy_used, predictions.strategy_used),
                               amount_placed = CASE
                                   WHEN excluded.amount_placed > 0
                                   THEN excluded.amount_placed
                                   ELSE predictions.amount_placed END""",
                        (event_id, timestamp, streamer, title, choice_index,
                         choice_title, choice_color, amount_placed, result,
                         points_gained, confidence, strategy_used, source),
                    )
                else:
                    conn.execute(
                        """INSERT INTO predictions
                               (timestamp, streamer, title, choice_index,
                                choice_title, choice_color, amount_placed, result,
                                points_gained, confidence, strategy_used, source)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (timestamp, streamer, title, choice_index,
                         choice_title, choice_color, amount_placed, result,
                         points_gained, confidence, strategy_used, source),
                    )
                conn.commit()
            finally:
                conn.close()

    def record_dry_run_results(self, timestamp, streamer, event_title,
                               active_strategy, dry_run_results,
                               prediction_id=None):
        """Persist all strategy results for a single prediction event."""
        if not dry_run_results:
            return
        rows = []
        for dr in dry_run_results:
            rows.append((
                prediction_id, timestamp, streamer, event_title,
                dr.get("strategy") if isinstance(dr, dict) else dr.strategy_name,
                dr.get("choice") if isinstance(dr, dict) else dr.choice,
                dr.get("outcome_title") if isinstance(dr, dict) else dr.outcome_title,
                dr.get("outcome_color") if isinstance(dr, dict) else dr.outcome_color,
                dr.get("amount") if isinstance(dr, dict) else dr.amount,
                dr.get("result_type") if isinstance(dr, dict) else dr.result_type,
                dr.get("points_gained") if isinstance(dr, dict) else dr.points_gained,
                1 if (dr.get("strategy") if isinstance(dr, dict) else dr.strategy_name) == active_strategy else 0,
            ))
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.executemany(
                    "INSERT INTO dry_run_results "
                    "(prediction_id, timestamp, streamer, event_title, "
                    "strategy, choice_index, outcome_title, outcome_color, "
                    "amount, result, points_gained, is_active) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
            finally:
                conn.close()

    def record_points(self, timestamp, streamer, amount, reason=None,
                      balance_after=None, source="runtime"):
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO points_history "
                    "(timestamp, streamer, amount, reason, balance_after, source) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (timestamp, streamer, amount, reason, balance_after, source),
                )
                conn.commit()
            finally:
                conn.close()

    def record_session(self, streamer, online_at=None, offline_at=None):
        with self._write_lock:
            conn = self._get_conn()
            try:
                if online_at and not offline_at:
                    conn.execute(
                        "INSERT INTO streamer_sessions (streamer, online_at) "
                        "VALUES (?, ?)",
                        (streamer, online_at),
                    )
                elif offline_at:
                    row = conn.execute(
                        "SELECT id, online_at FROM streamer_sessions "
                        "WHERE streamer=? AND offline_at IS NULL "
                        "ORDER BY id DESC LIMIT 1",
                        (streamer,),
                    ).fetchone()
                    if row:
                        try:
                            on_dt = datetime.fromisoformat(row["online_at"])
                            off_dt = datetime.fromisoformat(offline_at)
                            dur = int((off_dt - on_dt).total_seconds())
                        except Exception:
                            dur = None
                        conn.execute(
                            "UPDATE streamer_sessions "
                            "SET offline_at=?, duration_seconds=? WHERE id=?",
                            (offline_at, dur, row["id"]),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO streamer_sessions "
                            "(streamer, offline_at) VALUES (?, ?)",
                            (streamer, offline_at),
                        )
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Bulk import helpers
    # ------------------------------------------------------------------

    def _parse_points_from_detail(self, detail: str, result: str | None):
        """Extract integer points from a result_detail string like
        'WIN, Gained: +1.5k' or 'LOSE, Lost: -500'."""
        pts_match = re.search(r"[+-]?([\d.]+)\s*([kKMB]?)", detail)
        if not pts_match:
            return 0
        val = float(pts_match.group(1))
        suffix = pts_match.group(2).upper()
        if suffix == "K":
            val *= 1000
        elif suffix == "M":
            val *= 1_000_000
        points = int(val)
        if result == "LOSE" or "Lost" in detail:
            points = -abs(points)
        return points

    def import_from_log_event(self, event: dict, source="log"):
        """Import a single parsed log event (dict from _parse_log_line)."""
        etype = event.get("type")
        ts = event.get("timestamp")

        if etype == "prediction_result":
            detail = event.get("result_detail", "")
            result = None
            if "WIN" in detail.upper():
                result = "WIN"
            elif "LOSE" in detail.upper() or "LOST" in detail.upper():
                result = "LOSE"
            elif "REFUND" in detail.upper():
                result = "REFUND"
            points = self._parse_points_from_detail(detail, result)
            self.record_prediction(
                timestamp=ts,
                streamer=event.get("streamer"),
                title=event.get("title"),
                choice_index=event.get("choice_index"),
                choice_title=event.get("choice_title"),
                choice_color=event.get("choice_color"),
                result=result,
                points_gained=points,
                source=source,
            )

        elif etype == "points_gain":
            self.record_points(
                timestamp=ts,
                streamer=event.get("streamer"),
                amount=event.get("amount", 0),
                reason=event.get("reason"),
            )

        elif etype == "streamer_online":
            self.record_session(streamer=event.get("streamer"), online_at=ts)

        elif etype == "streamer_offline":
            self.record_session(streamer=event.get("streamer"), offline_at=ts)

        # Always record in the generic events table
        self.record_event(
            event_type=etype,
            streamer=event.get("streamer") or event.get("from_streamer"),
            data=event,
            timestamp=ts,
            source=source,
        )

    def import_log_file(self, filepath, parse_fn):
        """Bulk-import a log file.  *parse_fn* should be the
        ``_parse_log_line`` function from AnalyticsServer.
        Returns the number of events imported."""
        count = 0
        events_batch = []
        predictions_batch = []
        points_batch = []

        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    event = parse_fn(line.strip())
                    if event is None:
                        continue
                    count += 1
                    etype = event.get("type")
                    ts = event.get("timestamp")

                    # Collect into general events
                    events_batch.append((
                        ts, etype,
                        event.get("streamer") or event.get("from_streamer"),
                        json.dumps(event), "log",
                    ))

                    if etype == "prediction_result":
                        detail = event.get("result_detail", "")
                        result = None
                        if "WIN" in detail.upper():
                            result = "WIN"
                        elif "LOSE" in detail.upper() or "LOST" in detail.upper():
                            result = "LOSE"
                        elif "REFUND" in detail.upper():
                            result = "REFUND"
                        points = self._parse_points_from_detail(detail, result)
                        predictions_batch.append((
                            ts, event.get("streamer"), event.get("title"),
                            event.get("choice_index"), event.get("choice_title"),
                            event.get("choice_color"), 0, result, points, None, "log",
                        ))

                    elif etype == "points_gain":
                        points_batch.append((
                            ts, event.get("streamer"),
                            event.get("amount", 0), event.get("reason"), None, "log",
                        ))

        except Exception as exc:
            logger.warning("Error reading %s: %s", filepath, exc)

        # Batch-write everything in one transaction
        if events_batch or predictions_batch or points_batch:
            with self._write_lock:
                conn = self._get_conn()
                try:
                    conn.execute("BEGIN")
                    if events_batch:
                        conn.executemany(
                            "INSERT INTO events "
                            "(timestamp, event_type, streamer, data, source) "
                            "VALUES (?, ?, ?, ?, ?)",
                            events_batch,
                        )
                    if predictions_batch:
                        conn.executemany(
                            "INSERT INTO predictions "
                            "(timestamp, streamer, title, choice_index, "
                            "choice_title, choice_color, amount_placed, "
                            "result, points_gained, confidence, source) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            predictions_batch,
                        )
                    if points_batch:
                        conn.executemany(
                            "INSERT INTO points_history "
                            "(timestamp, streamer, amount, reason, "
                            "balance_after, source) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            points_batch,
                        )
                    conn.commit()
                except Exception as exc:
                    conn.rollback()
                    logger.warning("Batch insert failed for %s: %s", filepath, exc)
                finally:
                    conn.close()

        return count

    def import_all_logs(self, logs_dir, parse_fn):
        """Import every log file found in *logs_dir* (including rotated).
        Clears existing log-sourced data first to avoid duplicates.
        Returns ``{filename: count}``."""
        results = {}
        if not os.path.isdir(logs_dir):
            return results

        # Clear previous log imports so re-import is idempotent
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM events WHERE source='log'")
                conn.execute("DELETE FROM predictions WHERE source='log'")
                conn.execute("DELETE FROM points_history WHERE source='log'")
                conn.commit()
            finally:
                conn.close()

        files = sorted(
            (f for f in os.listdir(logs_dir)
             if f.endswith(".log") or ".log." in f),
        )
        for fname in files:
            full = os.path.join(logs_dir, fname)
            n = self.import_log_file(full, parse_fn)
            results[fname] = n
            logger.info("Telemetry: imported %d events from %s", n, fname)
        return results

    # ------------------------------------------------------------------
    # Read / query methods
    # ------------------------------------------------------------------

    def get_prediction_stats(self, streamer=None):
        """Aggregate prediction stats, optionally per streamer."""
        conn = self._get_conn()
        try:
            if streamer:
                rows = conn.execute(
                    "SELECT result, COUNT(*) AS cnt, "
                    "SUM(points_gained) AS net "
                    "FROM predictions WHERE streamer=? AND result IS NOT NULL "
                    "GROUP BY result",
                    (streamer,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT result, COUNT(*) AS cnt, "
                    "SUM(points_gained) AS net "
                    "FROM predictions WHERE result IS NOT NULL "
                    "GROUP BY result",
                ).fetchall()

            stats = {
                "wins": 0, "losses": 0, "refunds": 0,
                "net_points": 0, "total": 0,
            }
            for row in rows:
                r = (row["result"] or "").upper()
                if "WIN" in r:
                    stats["wins"] = row["cnt"]
                    stats["net_points"] += row["net"] or 0
                elif "LOSE" in r or "LOST" in r:
                    stats["losses"] = row["cnt"]
                    stats["net_points"] += row["net"] or 0
                elif "REFUND" in r:
                    stats["refunds"] = row["cnt"]
            stats["total"] = stats["wins"] + stats["losses"] + stats["refunds"]
            resolved = stats["wins"] + stats["losses"]
            stats["win_rate"] = round(
                stats["wins"] / max(resolved, 1) * 100, 1
            )
            return stats
        finally:
            conn.close()

    def get_streamer_prediction_stats(self):
        """Prediction stats grouped by streamer."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT streamer, result, COUNT(*) AS cnt, "
                "SUM(points_gained) AS net "
                "FROM predictions WHERE result IS NOT NULL "
                "GROUP BY streamer, result",
            ).fetchall()

            by_streamer: dict = {}
            for row in rows:
                s = row["streamer"]
                if s not in by_streamer:
                    by_streamer[s] = {
                        "wins": 0, "losses": 0, "refunds": 0, "net_points": 0,
                    }
                r = (row["result"] or "").upper()
                if "WIN" in r:
                    by_streamer[s]["wins"] = row["cnt"]
                    by_streamer[s]["net_points"] += row["net"] or 0
                elif "LOSE" in r or "LOST" in r:
                    by_streamer[s]["losses"] = row["cnt"]
                    by_streamer[s]["net_points"] += row["net"] or 0
                elif "REFUND" in r:
                    by_streamer[s]["refunds"] = row["cnt"]

            for st in by_streamer.values():
                st["total"] = st["wins"] + st["losses"] + st["refunds"]
                resolved = st["wins"] + st["losses"]
                st["win_rate"] = round(
                    st["wins"] / max(resolved, 1) * 100, 1
                )
            return by_streamer
        finally:
            conn.close()

    def get_event_counts(self):
        """Event count per type."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT event_type, COUNT(*) AS cnt FROM events "
                "GROUP BY event_type"
            ).fetchall()
            return {row["event_type"]: row["cnt"] for row in rows}
        finally:
            conn.close()

    def get_recent_predictions(self, limit=50, streamer=None):
        conn = self._get_conn()
        try:
            if streamer:
                rows = conn.execute(
                    "SELECT * FROM predictions WHERE streamer=? "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (streamer, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM predictions "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_db_summary(self):
        """Quick overview of what's in the database."""
        conn = self._get_conn()
        try:
            ev = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
            pr = conn.execute("SELECT COUNT(*) AS c FROM predictions").fetchone()["c"]
            pt = conn.execute("SELECT COUNT(*) AS c FROM points_history").fetchone()["c"]
            ss = conn.execute("SELECT COUNT(*) AS c FROM streamer_sessions").fetchone()["c"]
            dr = conn.execute("SELECT COUNT(*) AS c FROM dry_run_results").fetchone()["c"]
            return {
                "events": ev,
                "predictions": pr,
                "points_entries": pt,
                "sessions": ss,
                "dry_run_results": dr,
            }
        finally:
            conn.close()

    def get_dry_run_summary(self, streamer=None):
        """Aggregate dry-run results per strategy for a streamer.
        Returns list of dicts: [{strategy, total, wins, losses, refunds,
                                  net_points, win_rate, is_active, is_best}]"""
        conn = self._get_conn()
        try:
            if streamer:
                rows = conn.execute(
                    "SELECT strategy, is_active, result, "
                    "COUNT(*) AS cnt, SUM(points_gained) AS net "
                    "FROM dry_run_results WHERE streamer=? AND result IS NOT NULL "
                    "GROUP BY strategy, result",
                    (streamer,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT strategy, is_active, result, "
                    "COUNT(*) AS cnt, SUM(points_gained) AS net "
                    "FROM dry_run_results WHERE result IS NOT NULL "
                    "GROUP BY strategy, result",
                ).fetchall()

            summary = {}
            for row in rows:
                s = row["strategy"]
                if s not in summary:
                    summary[s] = {
                        "strategy": s,
                        "total": 0, "wins": 0, "losses": 0, "refunds": 0,
                        "net_points": 0, "is_active": bool(row["is_active"]),
                    }
                r = (row["result"] or "").upper()
                if r == "WIN":
                    summary[s]["wins"] = row["cnt"]
                    summary[s]["net_points"] += row["net"] or 0
                elif r == "LOSE":
                    summary[s]["losses"] = row["cnt"]
                    summary[s]["net_points"] += row["net"] or 0
                elif r == "REFUND":
                    summary[s]["refunds"] = row["cnt"]

            result = []
            for st in summary.values():
                st["total"] = st["wins"] + st["losses"] + st["refunds"]
                resolved = st["wins"] + st["losses"]
                st["win_rate"] = round(
                    st["wins"] / max(resolved, 1) * 100, 1
                )
                result.append(st)

            result.sort(key=lambda x: x["net_points"], reverse=True)
            # Mark is_best on the top non-ACTIVE strategy (ACTIVE cannot be
            # switched to, so it should never be flagged as the actionable best)
            for r in result:
                r["is_best"] = False
            for r in result:
                if r["strategy"] != "ACTIVE":
                    r["is_best"] = True
                    break
            return result
        finally:
            conn.close()

    def get_dry_run_history(self, streamer=None, limit=200):
        """Return recent dry-run predictions with all strategy outcomes.
        Groups by (timestamp, event_title) and nests strategy results."""
        conn = self._get_conn()
        try:
            if streamer:
                rows = conn.execute(
                    "SELECT * FROM dry_run_results WHERE streamer=? "
                    "ORDER BY timestamp DESC, id ASC LIMIT ?",
                    (streamer, limit * 20),  # approx 20 strategies per prediction
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM dry_run_results "
                    "ORDER BY timestamp DESC, id ASC LIMIT ?",
                    (limit * 20,),
                ).fetchall()

            # Group by (timestamp, event_title)
            predictions = {}
            order = []
            for row in rows:
                key = (row["timestamp"], row["event_title"])
                if key not in predictions:
                    predictions[key] = {
                        "timestamp": row["timestamp"],
                        "event_title": row["event_title"],
                        "streamer": row["streamer"],
                        "active_strategy": None,
                        "strategies": [],
                    }
                    order.append(key)
                if row["is_active"]:
                    predictions[key]["active_strategy"] = row["strategy"]
                predictions[key]["strategies"].append({
                    "strategy": row["strategy"],
                    "choice": row["choice_index"],
                    "amount": row["amount"],
                    "outcome_title": row["outcome_title"],
                    "outcome_color": row["outcome_color"],
                    "result_type": row["result"],
                    "points_gained": row["points_gained"],
                })

            return [predictions[k] for k in order[:limit]]
        finally:
            conn.close()

    def has_dry_run_data(self, streamer=None):
        """Check if there's any dry-run strategy data."""
        conn = self._get_conn()
        try:
            if streamer:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM dry_run_results WHERE streamer=?",
                    (streamer,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM dry_run_results"
                ).fetchone()
            return row["c"] > 0
        finally:
            conn.close()

    def backfill_dry_run_from_predictions(self, streamer=None):
        """For historical predictions that lack dry-run strategy data,
        create simulated results for deterministic strategies.
        For 2-outcome predictions we know which outcome won, so we can
        simulate NUMBER_1, NUMBER_2, CONTRARIAN (least-voted = index 1
        for typical 2-choice), and MOST_VOTED (index 0 for typical 2-choice).
        Returns count of predictions backfilled."""
        conn = self._get_conn()
        try:
            # Get predictions that don't have dry_run_results yet
            if streamer:
                preds = conn.execute(
                    "SELECT p.* FROM predictions p "
                    "LEFT JOIN dry_run_results dr "
                    "  ON p.timestamp = dr.timestamp AND p.streamer = dr.streamer "
                    "WHERE p.streamer=? AND p.result IS NOT NULL "
                    "  AND dr.id IS NULL "
                    "ORDER BY p.timestamp",
                    (streamer,),
                ).fetchall()
            else:
                preds = conn.execute(
                    "SELECT p.* FROM predictions p "
                    "LEFT JOIN dry_run_results dr "
                    "  ON p.timestamp = dr.timestamp AND p.streamer = dr.streamer "
                    "WHERE p.result IS NOT NULL AND dr.id IS NULL "
                    "ORDER BY p.timestamp",
                ).fetchall()

            if not preds:
                return 0

            # For each prediction, simulate deterministic strategies
            # We know: choice_index (what active strategy picked), result (WIN/LOSE/REFUND)
            # For 2-outcome: if result=WIN, choice_index won; if result=LOSE, 1-choice_index won
            strategies_to_sim = [
                "MOST_VOTED", "HIGH_ODDS", "PERCENTAGE", "SMART_MONEY",
                "SMART", "CONTRARIAN", "NUMBER_1", "NUMBER_2",
            ]

            rows_to_insert = []
            count = 0
            for pred in preds:
                result = (pred["result"] or "").upper()
                choice_idx = pred["choice_index"]
                pts = pred["points_gained"] or 0
                amount = abs(pts) if pts != 0 else (pred["amount_placed"] or 0)

                if result == "REFUND":
                    winning_index = None
                elif result == "WIN":
                    winning_index = choice_idx
                elif result == "LOSE":
                    winning_index = 1 - choice_idx if choice_idx is not None else None
                else:
                    continue

                # The active strategy's actual result
                rows_to_insert.append((
                    pred["id"], pred["timestamp"], pred["streamer"],
                    pred["title"], "ACTIVE",
                    choice_idx, pred["choice_title"], pred["choice_color"],
                    amount, result, pts, 1,
                ))

                # Simulate fixed-choice strategies
                for strat in strategies_to_sim:
                    if strat == "NUMBER_1":
                        sim_choice = 0
                    elif strat == "NUMBER_2":
                        sim_choice = 1
                    elif strat in ("MOST_VOTED", "SMART", "PERCENTAGE", "SMART_MONEY"):
                        # Without odds data, assume these pick index 0 (majority)
                        sim_choice = 0
                    elif strat in ("HIGH_ODDS", "CONTRARIAN"):
                        # These typically pick the underdog = index 1
                        sim_choice = 1
                    else:
                        continue

                    if result == "REFUND":
                        sim_result = "REFUND"
                        sim_pts = 0
                    elif winning_index is not None and sim_choice == winning_index:
                        sim_result = "WIN"
                        sim_pts = abs(pts) if pts > 0 and sim_choice == choice_idx else amount
                    else:
                        sim_result = "LOSE"
                        sim_pts = -amount

                    rows_to_insert.append((
                        pred["id"], pred["timestamp"], pred["streamer"],
                        pred["title"], strat,
                        sim_choice, None, None,
                        amount, sim_result, sim_pts, 0,
                    ))

                count += 1

        finally:
            conn.close()

        if rows_to_insert:
            with self._write_lock:
                conn = self._get_conn()
                try:
                    conn.executemany(
                        "INSERT INTO dry_run_results "
                        "(prediction_id, timestamp, streamer, event_title, "
                        "strategy, choice_index, outcome_title, outcome_color, "
                        "amount, result, points_gained, is_active) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        rows_to_insert,
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    logger.warning("Backfill dry-run failed", exc_info=True)
                finally:
                    conn.close()

        return count

    def record_strategy_switch(self, streamer, old_strategy, new_strategy,
                               reason="manual", timestamp=None):
        if timestamp is None:
            timestamp = datetime.now().isoformat()
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT INTO strategy_switches "
                    "(timestamp, streamer, old_strategy, new_strategy, reason, source) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (timestamp, streamer, old_strategy, new_strategy, reason, reason),
                )
                conn.commit()
            finally:
                conn.close()

    def get_best_strategy(self, streamer):
        """Return the best-performing switchable strategy name for a streamer.

        Excludes ACTIVE since it is not a user-selectable strategy.
        Returns None if no suitable strategy is found.
        """
        summary = self.get_dry_run_summary(streamer)
        if not summary:
            return None
        for s in summary:
            if s["strategy"] != "ACTIVE":
                return s["strategy"]
        return None

    def get_channel_log(self, streamer, limit=100):
        """Return a chronological event log for a streamer, combining events,
        predictions, and points into a unified timeline."""
        conn = self._get_conn()
        try:
            entries = []
            # Events
            rows = conn.execute(
                "SELECT timestamp, event_type, data, source FROM events "
                "WHERE streamer=? ORDER BY timestamp DESC LIMIT ?",
                (streamer, limit),
            ).fetchall()
            for r in rows:
                data = None
                if r["data"]:
                    try:
                        data = json.loads(r["data"])
                    except Exception:
                        data = {"raw": r["data"]}
                entries.append({
                    "timestamp": r["timestamp"],
                    "category": "event",
                    "event_type": r["event_type"],
                    "data": data,
                    "source": r["source"],
                })
            # Predictions
            rows = conn.execute(
                "SELECT timestamp, event_id, title, choice_title, choice_color, "
                "amount_placed, result, points_gained, strategy_used, source "
                "FROM predictions WHERE streamer=? ORDER BY timestamp DESC LIMIT ?",
                (streamer, limit),
            ).fetchall()
            for r in rows:
                entries.append({
                    "timestamp": r["timestamp"],
                    "category": "prediction",
                    "event_type": "BET_WIN" if r["result"] == "WIN"
                        else ("BET_LOSE" if r["result"] == "LOSE" else "BET_REFUND"),
                    "data": {
                        "title": r["title"],
                        "choice": r["choice_title"],
                        "color": r["choice_color"],
                        "amount": r["amount_placed"],
                        "result": r["result"],
                        "points_gained": r["points_gained"],
                        "strategy": r["strategy_used"],
                    },
                    "source": r["source"],
                })
            # Points
            rows = conn.execute(
                "SELECT timestamp, amount, reason, balance_after, source "
                "FROM points_history WHERE streamer=? ORDER BY timestamp DESC LIMIT ?",
                (streamer, limit),
            ).fetchall()
            for r in rows:
                entries.append({
                    "timestamp": r["timestamp"],
                    "category": "points",
                    "event_type": f"GAIN_FOR_{(r['reason'] or 'WATCH').upper()}",
                    "data": {
                        "amount": r["amount"],
                        "reason": r["reason"],
                        "balance": r["balance_after"],
                    },
                    "source": r["source"],
                })
            # Sort by timestamp descending
            entries.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
            return entries[:limit]
        finally:
            conn.close()

    def get_all_streamers(self) -> list:
        """Return every distinct streamer name recorded in the telemetry DB."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT streamer FROM events WHERE streamer IS NOT NULL "
                "UNION "
                "SELECT DISTINCT streamer FROM predictions WHERE streamer IS NOT NULL "
                "ORDER BY streamer"
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()

    def export_all_tables(self):
        """Export all telemetry data as a JSON-serializable dict."""
        conn = self._get_conn()
        try:
            tables = {}
            for tbl in ("events", "predictions", "dry_run_results",
                        "points_history", "streamer_sessions", "strategy_switches"):
                rows = conn.execute(f"SELECT * FROM {tbl}").fetchall()
                tables[tbl] = [dict(r) for r in rows]
            return tables
        finally:
            conn.close()

    def import_from_json_dump(self, data: dict):
        """Import data from a JSON dump (from export_all_tables).
        Merges into the current DB, skipping duplicates."""
        imported = {}
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                for tbl in ("events", "predictions", "points_history",
                            "streamer_sessions", "strategy_switches", "dry_run_results"):
                    rows = data.get(tbl, [])
                    if not rows:
                        imported[tbl] = 0
                        continue
                    count = 0
                    for row in rows:
                        cols = [k for k in row.keys() if k != "id"]
                        vals = [row[k] for k in cols]
                        placeholders = ", ".join(["?"] * len(cols))
                        col_names = ", ".join(cols)
                        try:
                            conn.execute(
                                f"INSERT OR IGNORE INTO {tbl} ({col_names}) "
                                f"VALUES ({placeholders})",
                                vals,
                            )
                            count += 1
                        except Exception:
                            pass
                    imported[tbl] = count
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        return imported

    def get_consecutive_best_count(self, streamer, n_recent=None):
        """Check how many of the most recent N predictions the best strategy
        has been the top performer. Returns (best_strategy, consecutive_count)."""
        conn = self._get_conn()
        try:
            best = self.get_best_strategy(streamer)
            if not best:
                return None, 0

            # Get distinct predictions ordered by most recent
            query = (
                "SELECT DISTINCT timestamp, event_title "
                "FROM dry_run_results WHERE streamer=? AND result IS NOT NULL "
                "ORDER BY timestamp DESC"
            )
            if n_recent:
                query += f" LIMIT {int(n_recent)}"
            preds = conn.execute(query, (streamer,)).fetchall()

            consecutive = 0
            for pred in preds:
                # For this prediction, which strategy had the highest points?
                rows = conn.execute(
                    "SELECT strategy, SUM(points_gained) AS net "
                    "FROM dry_run_results "
                    "WHERE streamer=? AND timestamp=? AND event_title=? "
                    "  AND result IS NOT NULL "
                    "GROUP BY strategy ORDER BY net DESC LIMIT 1",
                    (streamer, pred["timestamp"], pred["event_title"]),
                ).fetchone()
                if rows and rows["strategy"] == best:
                    consecutive += 1
                else:
                    break

            return best, consecutive
        finally:
            conn.close()
