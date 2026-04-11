import ast
import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from threading import Thread

import pandas as pd
import requests
from flask import Flask, Response, cli, render_template, request

from TwitchChannelPointsMiner.classes.Discord import AVATAR_URL
from TwitchChannelPointsMiner.classes.Settings import Settings
from TwitchChannelPointsMiner.classes.Telemetry import Telemetry
from TwitchChannelPointsMiner.utils import download_file

cli.show_server_banner = lambda *_: None
logger = logging.getLogger(__name__)

# Module-level telemetry instance (initialised lazily in AnalyticsServer)
_telemetry: Telemetry | None = None


# Patterns that should not appear in a config file
_DANGEROUS_PATTERNS = [
    (r"\bos\.system\b", "os.system() call"),
    (r"\bsubprocess\b", "subprocess module usage"),
    (r"\b__import__\b", "__import__() call"),
    (r"\beval\s*\(", "eval() call"),
    (r"\bexec\s*\(", "exec() call"),
    (r"\bglobals\s*\(", "globals() call"),
    (r"\bos\.remove\b", "os.remove() call"),
    (r"\bshutil\.rmtree\b", "shutil.rmtree() call"),
]


def _check_dangerous_patterns(content: str):
    """Return a warning string if dangerous code is found, else None."""
    for pattern, desc in _DANGEROUS_PATTERNS:
        match = re.search(pattern, content)
        if match:
            line_num = content[: match.start()].count("\n") + 1
            return f"Potentially dangerous code on line {line_num}: {desc}"
    return None


def streamers_available():
    path = Settings.analytics_path
    return [
        f
        for f in os.listdir(path)
        if os.path.isfile(os.path.join(path, f)) and f.endswith(".json")
    ]


def aggregate(df, freq="30Min"):
    df_base_events = df[(df.z == "Watch") | (df.z == "Claim")]
    df_other_events = df[(df.z != "Watch") & (df.z != "Claim")]

    be = df_base_events.groupby(
        [pd.Grouper(freq=freq, key="datetime"), "z"]).max()
    be = be.reset_index()

    oe = df_other_events.groupby(
        [pd.Grouper(freq=freq, key="datetime"), "z"]).max()
    oe = oe.reset_index()

    result = pd.concat([be, oe])
    return result


def filter_datas(start_date, end_date, datas):
    # Note: https://stackoverflow.com/questions/4676195/why-do-i-need-to-multiply-unix-timestamps-by-1000-in-javascript
    start_date = (
        datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000
        if start_date is not None
        else 0
    )
    end_date = (
        datetime.strptime(end_date, "%Y-%m-%d")
        if end_date is not None
        else datetime.now()
    ).replace(hour=23, minute=59, second=59).timestamp() * 1000

    if "series" in datas:
        original_series = datas["series"]
        df = pd.DataFrame(datas["series"])
        df["datetime"] = pd.to_datetime(df.x // 1000, unit="s")

        df = df[(df.x >= start_date) & (df.x <= end_date)]

        datas["series"] = (
            df.drop(columns="datetime")
            .sort_values(by=["x", "y"], ascending=True)
            .to_dict("records")
        )
    else:
        original_series = []
        datas["series"] = []

    # If no data is found within the timeframe, that usually means the streamer hasn't streamed within that timeframe
    # We create a series that shows up as a straight line on the dashboard, with 'No Stream' as labels
    if len(datas["series"]) == 0 and original_series:
        new_end_date = start_date
        new_start_date = 0
        df = pd.DataFrame(original_series)
        df["datetime"] = pd.to_datetime(df.x // 1000, unit="s")

        # Attempt to get the last known balance from before the provided timeframe
        df = df[(df.x >= new_start_date) & (df.x <= new_end_date)]
        last_balance = df.drop(columns="datetime").sort_values(
            by=["x", "y"], ascending=True).to_dict("records")[-1]['y']

        datas["series"] = [{'x': start_date, 'y': last_balance, 'z': 'No Stream'}, {
            'x': end_date, 'y': last_balance, 'z': 'No Stream'}]

    if "annotations" in datas:
        df = pd.DataFrame(datas["annotations"])
        df["datetime"] = pd.to_datetime(df.x // 1000, unit="s")

        df = df[(df.x >= start_date) & (df.x <= end_date)]

        datas["annotations"] = (
            df.drop(columns="datetime")
            .sort_values(by="x", ascending=True)
            .to_dict("records")
        )
    else:
        datas["annotations"] = []

    return datas


def read_json(streamer, return_response=True):
    start_date = request.args.get("startDate", type=str)
    end_date = request.args.get("endDate", type=str)

    path = Settings.analytics_path
    streamer = streamer if streamer.endswith(".json") else f"{streamer}.json"

    # Check if the file exists before attempting to read it
    if not os.path.exists(os.path.join(path, streamer)):
        error_message = f"File '{streamer}' not found."
        logger.error(error_message)
        if return_response:
            return Response(json.dumps({"error": error_message}), status=404, mimetype="application/json")
        else:
            return {"error": error_message}

    try:
        with open(os.path.join(path, streamer), 'r') as file:
            data = json.load(file)
    except json.JSONDecodeError as e:
        error_message = f"Error decoding JSON in file '{streamer}': {str(e)}"
        logger.error(error_message)
        if return_response:
            return Response(json.dumps({"error": error_message}), status=500, mimetype="application/json")
        else:
            return {"error": error_message}

    # Handle filtering data, if applicable
    filtered_data = filter_datas(start_date, end_date, data)
    if return_response:
        return Response(json.dumps(filtered_data), status=200, mimetype="application/json")
    else:
        return filtered_data


def get_challenge_points(streamer):
    datas = read_json(streamer, return_response=False)
    if "series" in datas and datas["series"]:
        return datas["series"][-1]["y"]
    return 0  # Default value when 'series' key is not found or empty


def get_last_activity(streamer):
    datas = read_json(streamer, return_response=False)
    if "series" in datas and datas["series"]:
        return datas["series"][-1]["x"]
    return 0  # Default value when 'series' key is not found or empty


def json_all():
    return Response(
        json.dumps(
            [
                {
                    "name": streamer.strip(".json"),
                    "data": read_json(streamer, return_response=False),
                }
                for streamer in streamers_available()
            ]
        ),
        status=200,
        mimetype="application/json",
    )


def index(refresh=5, days_ago=7):
    return render_template(
        "charts.html",
        refresh=(refresh * 60 * 1000),
        daysAgo=days_ago,
    )


def streamers():
    return Response(
        json.dumps(
            [
                {"name": s, "points": get_challenge_points(
                    s), "last_activity": get_last_activity(s)}
                for s in sorted(streamers_available())
            ]
        ),
        status=200,
        mimetype="application/json",
    )


def dry_run(streamer):
    """Return prediction history for a streamer with all strategy results.
    Prefers dry_run_results table; falls back to predictions table; then JSON."""
    global _telemetry
    name = streamer.replace(".json", "")

    # Try SQLite dry_run_results first (multi-strategy)
    try:
        if _telemetry is None:
            db_path = os.path.join(Settings.analytics_path, "telemetry.db")
            if os.path.isfile(db_path):
                _telemetry = Telemetry(db_path)
        if _telemetry is not None:
            if _telemetry.has_dry_run_data(name):
                history = _telemetry.get_dry_run_history(streamer=name, limit=200)
                if history:
                    return Response(
                        json.dumps(history), status=200, mimetype="application/json"
                    )
            # Fall back to flat predictions
            preds = _telemetry.get_recent_predictions(limit=200, streamer=name)
            if preds:
                return Response(
                    json.dumps(preds), status=200, mimetype="application/json"
                )
    except Exception:
        logger.debug("Telemetry DB not available for dry_run", exc_info=True)

    # Fall back to JSON
    path = Settings.analytics_path
    streamer_file = streamer if streamer.endswith(".json") else f"{streamer}.json"

    if not os.path.exists(os.path.join(path, streamer_file)):
        return Response(
            json.dumps({"error": f"File '{streamer_file}' not found."}),
            status=404,
            mimetype="application/json",
        )

    try:
        with open(os.path.join(path, streamer_file), "r") as file:
            data = json.load(file)
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON in file '{streamer_file}'")
        return Response(
            json.dumps({"error": "Error decoding analytics data."}),
            status=500,
            mimetype="application/json",
        )

    results = data.get("dry_run_predictions", [])
    return Response(
        json.dumps(results), status=200, mimetype="application/json"
    )


def dry_run_summary(streamer):
    """Return per-strategy prediction summary for a streamer.
    Prefers dry_run_results table; falls back to predictions; then JSON."""
    global _telemetry
    name = streamer.replace(".json", "")

    # Determine which strategy is currently configured for this channel
    current_strategy = ""
    try:
        cfg, _ = _read_settings_json()
        if cfg:
            current_strategy = _get_channel_strategy(cfg, name)
    except Exception:
        pass

    # Try SQLite dry_run_results (multi-strategy)
    try:
        if _telemetry is None:
            db_path = os.path.join(Settings.analytics_path, "telemetry.db")
            if os.path.isfile(db_path):
                _telemetry = Telemetry(db_path)
        if _telemetry is not None:
            if _telemetry.has_dry_run_data(name):
                result = _telemetry.get_dry_run_summary(name)
                if result:
                    # Mark the currently configured strategy as active
                    for r in result:
                        r["is_active"] = (
                            r["strategy"].upper() == current_strategy.upper()
                            if current_strategy else r.get("is_active", False)
                        )
                    return Response(
                        json.dumps({
                            "strategies": result,
                            "current_strategy": current_strategy,
                        }),
                        status=200, mimetype="application/json",
                    )
            # Fall back to single "Active" strategy from predictions
            stats = _telemetry.get_prediction_stats(name)
            if stats and stats.get("total", 0) > 0:
                result = [{
                    "strategy": current_strategy or "Active",
                    "total": stats["total"],
                    "wins": stats["wins"],
                    "losses": stats["losses"],
                    "refunds": stats["refunds"],
                    "net_points": stats["net_points"],
                    "win_rate": stats["win_rate"],
                    "is_active": True,
                    "is_best": True,
                }]
                return Response(
                    json.dumps({
                        "strategies": result,
                        "current_strategy": current_strategy,
                    }),
                    status=200, mimetype="application/json",
                )
    except Exception:
        logger.debug("Telemetry DB not available for dry_run_summary", exc_info=True)

    # Fall back to JSON
    path = Settings.analytics_path
    streamer_file = streamer if streamer.endswith(".json") else f"{streamer}.json"

    if not os.path.exists(os.path.join(path, streamer_file)):
        return Response(
            json.dumps({"error": f"File '{streamer_file}' not found."}),
            status=404,
            mimetype="application/json",
        )

    try:
        with open(os.path.join(path, streamer_file), "r") as file:
            data = json.load(file)
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON in file '{streamer_file}'")
        return Response(
            json.dumps({"error": "Error decoding analytics data."}),
            status=500,
            mimetype="application/json",
        )

    predictions = data.get("dry_run_predictions", [])
    summary = {}

    for pred in predictions:
        active = pred.get("active_strategy", "")
        for s in pred.get("strategies", []):
            name = s.get("strategy", "")
            if name not in summary:
                summary[name] = {
                    "strategy": name,
                    "total": 0,
                    "wins": 0,
                    "losses": 0,
                    "refunds": 0,
                    "net_points": 0,
                    "is_active": name == active,
                }
            summary[name]["total"] += 1
            rt = s.get("result_type")
            if rt == "WIN":
                summary[name]["wins"] += 1
            elif rt == "LOSE":
                summary[name]["losses"] += 1
            elif rt == "REFUND":
                summary[name]["refunds"] += 1
            summary[name]["net_points"] += s.get("points_gained", 0)
            # Update is_active to the latest
            summary[name]["is_active"] = name == active

    result = sorted(summary.values(), key=lambda x: x["net_points"], reverse=True)

    # Mark the best performer
    if result:
        result[0]["is_best"] = True
        for r in result[1:]:
            r["is_best"] = False
        for r in result:
            resolved = r["total"] - r["refunds"]
            r["win_rate"] = (
                round(r["wins"] / resolved * 100, 1)
                if resolved > 0
                else 0.0
            )

    return Response(
        json.dumps({
            "strategies": result,
            "current_strategy": current_strategy,
        }),
        status=200, mimetype="application/json",
    )


def download_assets(assets_folder, required_files):
    Path(assets_folder).mkdir(parents=True, exist_ok=True)
    logger.info(f"Downloading assets to {assets_folder}")

    for f in required_files:
        if os.path.isfile(os.path.join(assets_folder, f)) is False:
            if (
                download_file(os.path.join("assets", f),
                              os.path.join(assets_folder, f))
                is True
            ):
                logger.info(f"Downloaded {f}")


def config_editor_page():
    return render_template("config_editor.html")


def config_read():
    """Read the current config file. Prefers settings.json, falls back to run.py."""
    # Try settings.json first (new format)
    settings_path = os.path.join(Path().absolute(), "settings.json")
    if os.path.isfile(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                content = f.read()
            # Validate it's real JSON
            json.loads(content)
            return Response(
                json.dumps({"content": content, "path": settings_path, "format": "json"}),
                status=200,
                mimetype="application/json",
            )
        except Exception:
            pass  # Fall through to run.py

    config_path = os.path.join(Path().absolute(), "run.py")
    if not os.path.isfile(config_path):
        # Try example.py as fallback
        config_path = os.path.join(Path().absolute(), "example.py")
    if not os.path.isfile(config_path):
        return Response(
            json.dumps({"error": "No config file found (settings.json, run.py, or example.py)."}),
            status=404,
            mimetype="application/json",
        )
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
        return Response(
            json.dumps({"content": content, "path": config_path, "format": "python"}),
            status=200,
            mimetype="application/json",
        )
    except Exception:
        return Response(
            json.dumps({"error": "Failed to read config file."}),
            status=500,
            mimetype="application/json",
        )


def config_validate():
    """Validate submitted config content (auto-detects JSON or Python)."""
    data = request.get_json(silent=True)
    if not data or "content" not in data:
        return Response(
            json.dumps({"valid": False, "error": "No content provided."}),
            status=400,
            mimetype="application/json",
        )
    content = data["content"]
    fmt = data.get("format", "auto")

    # Auto-detect format
    if fmt == "auto":
        stripped = content.lstrip()
        fmt = "json" if stripped.startswith("{") or stripped.startswith("[") else "python"

    if fmt == "json":
        try:
            json.loads(content)
            return Response(
                json.dumps({"valid": True, "format": "json"}),
                status=200,
                mimetype="application/json",
            )
        except json.JSONDecodeError as e:
            return Response(
                json.dumps({
                    "valid": False,
                    "format": "json",
                    "error": f"Line {e.lineno}: {e.msg}",
                    "lineno": e.lineno,
                }),
                status=200,
                mimetype="application/json",
            )
    else:
        # Python validation
        danger = _check_dangerous_patterns(content)
        if danger:
            return Response(
                json.dumps({"valid": False, "format": "python", "error": danger}),
                status=200,
                mimetype="application/json",
            )
        try:
            ast.parse(content)
            return Response(
                json.dumps({"valid": True, "format": "python"}),
                status=200,
                mimetype="application/json",
            )
        except SyntaxError as e:
            return Response(
                json.dumps({
                    "valid": False,
                    "format": "python",
                    "error": f"Line {e.lineno}: {e.msg}",
                    "lineno": e.lineno,
                }),
                status=200,
                mimetype="application/json",
            )


def config_save():
    """Save config after validation, creating a backup first.
    Auto-detects JSON vs Python format."""
    data = request.get_json(silent=True)
    if not data or "content" not in data:
        return Response(
            json.dumps({"success": False, "error": "No content provided."}),
            status=400,
            mimetype="application/json",
        )
    content = data["content"]
    fmt = data.get("format", "auto")

    # Auto-detect format
    if fmt == "auto":
        stripped = content.lstrip()
        fmt = "json" if stripped.startswith("{") or stripped.startswith("[") else "python"

    if fmt == "json":
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            return Response(
                json.dumps({
                    "success": False,
                    "error": f"JSON error on line {e.lineno}: {e.msg}",
                }),
                status=400,
                mimetype="application/json",
            )
        config_path = os.path.join(Path().absolute(), "settings.json")
    else:
        # Python: safety + syntax checks
        danger = _check_dangerous_patterns(content)
        if danger:
            return Response(
                json.dumps({"success": False, "error": danger}),
                status=400,
                mimetype="application/json",
            )
        try:
            ast.parse(content)
        except SyntaxError as e:
            return Response(
                json.dumps({
                    "success": False,
                    "error": f"Syntax error on line {e.lineno}: {e.msg}",
                }),
                status=400,
                mimetype="application/json",
            )
        config_path = os.path.join(Path().absolute(), "run.py")

    # Create backup
    backup_path = config_path + ".bak"
    try:
        if os.path.isfile(config_path):
            shutil.copy2(config_path, backup_path)
    except Exception:
        logger.warning("Failed to create config backup")

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"Config file saved via web editor ({fmt} format)")
        return Response(
            json.dumps({"success": True, "path": config_path}),
            status=200,
            mimetype="application/json",
        )
    except Exception:
        return Response(
            json.dumps({"success": False, "error": "Failed to write config file."}),
            status=500,
            mimetype="application/json",
        )


_server_start_time = None


def health():
    """Return basic health status of the miner."""
    import time

    uptime = None
    if _server_start_time is not None:
        uptime = int(time.time() - _server_start_time)

    path = Settings.analytics_path
    streamer_count = 0
    try:
        streamer_count = len(streamers_available())
    except Exception:
        pass

    return Response(
        json.dumps({
            "status": "ok",
            "uptime_seconds": uptime,
            "streamers_tracked": streamer_count,
        }),
        status=200,
        mimetype="application/json",
    )


def config_export_runpy():
    """Export the current settings.json as an upstream-compatible run.py.
    Returns the generated Python source as a download."""
    settings_path = os.path.join(Path().absolute(), "settings.json")
    if not os.path.isfile(settings_path):
        return Response(
            json.dumps({"error": "No settings.json found to export."}),
            status=404,
            mimetype="application/json",
        )
    try:
        # Import export module from project root
        import importlib.util
        export_path = os.path.join(Path().absolute(), "export.py")
        spec = importlib.util.spec_from_file_location("export", export_path)
        export_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(export_mod)

        with open(settings_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        # Generate to a temporary path
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as tmp:
            tmp_path = tmp.name
        export_mod.export(cfg, tmp_path)

        with open(tmp_path, "r", encoding="utf-8") as f:
            content = f.read()
        os.unlink(tmp_path)

        return Response(
            content,
            status=200,
            mimetype="text/x-python",
            headers={"Content-Disposition": "attachment; filename=run.py"},
        )
    except Exception as e:
        logger.error(f"Export failed: {e}")
        return Response(
            json.dumps({"error": f"Export failed: {str(e)}"}),
            status=500,
            mimetype="application/json",
        )


def export_csv():
    """Export a streamer's analytics data as CSV."""
    streamer = request.args.get("streamer", "")
    if not streamer:
        return Response(
            json.dumps({"error": "Missing 'streamer' parameter."}),
            status=400,
            mimetype="application/json",
        )
    streamer_file = streamer if streamer.endswith(".json") else f"{streamer}.json"
    path = Settings.analytics_path

    if not os.path.exists(os.path.join(path, streamer_file)):
        return Response(
            json.dumps({"error": f"File '{streamer_file}' not found."}),
            status=404,
            mimetype="application/json",
        )

    try:
        with open(os.path.join(path, streamer_file), "r") as f:
            data = json.load(f)
        series = data.get("series", [])
        if not series:
            return Response("timestamp,points,reason\n", status=200, mimetype="text/csv")

        lines = ["timestamp,points,reason"]
        for entry in series:
            ts = datetime.utcfromtimestamp(entry["x"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"{ts},{entry.get('y', 0)},{entry.get('z', '')}")

        csv_content = "\n".join(lines) + "\n"
        safe_name = streamer.replace(".json", "").replace("/", "_").replace("\\", "_")
        return Response(
            csv_content,
            status=200,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={safe_name}_analytics.csv"},
        )
    except Exception as e:
        logger.error(f"CSV export failed: {e}")
        return Response(
            json.dumps({"error": str(e)}),
            status=500,
            mimetype="application/json",
        )


def export_json():
    """Export a streamer's raw analytics data as JSON download."""
    streamer = request.args.get("streamer", "")
    if not streamer:
        return Response(
            json.dumps({"error": "Missing 'streamer' parameter."}),
            status=400,
            mimetype="application/json",
        )
    streamer_file = streamer if streamer.endswith(".json") else f"{streamer}.json"
    path = Settings.analytics_path

    if not os.path.exists(os.path.join(path, streamer_file)):
        return Response(
            json.dumps({"error": f"File '{streamer_file}' not found."}),
            status=404,
            mimetype="application/json",
        )

    try:
        with open(os.path.join(path, streamer_file), "r") as f:
            data = json.load(f)

        safe_name = streamer.replace(".json", "").replace("/", "_").replace("\\", "_")
        return Response(
            json.dumps(data, indent=2),
            status=200,
            mimetype="application/json",
            headers={"Content-Disposition": f"attachment; filename={safe_name}_analytics.json"},
        )
    except Exception as e:
        logger.error(f"JSON export failed: {e}")
        return Response(
            json.dumps({"error": str(e)}),
            status=500,
            mimetype="application/json",
        )


def global_stats():
    """Aggregate stats across all tracked streamers.
    Uses SQLite telemetry for prediction data when available."""
    global _telemetry

    path = Settings.analytics_path
    available = streamers_available()

    # Try to get prediction stats from SQLite telemetry
    sqlite_pred_stats = {}
    try:
        if _telemetry is None:
            db_path = os.path.join(path, "telemetry.db")
            if os.path.isfile(db_path):
                _telemetry = Telemetry(db_path)
        if _telemetry is not None:
            sqlite_pred_stats = _telemetry.get_streamer_prediction_stats()
    except Exception:
        logger.debug("Telemetry DB not available for global_stats", exc_info=True)

    total_points_earned = 0
    total_predictions = 0
    total_wins = 0
    total_losses = 0
    total_refunds = 0
    net_prediction_points = 0
    streamer_stats = []
    total_time_tracked = 0

    for s in available:
        try:
            with open(os.path.join(path, s), "r") as f:
                data = json.load(f)
        except Exception:
            continue

        name = s.replace(".json", "")
        series = data.get("series", [])
        predictions = data.get("dry_run_predictions", [])

        # Points delta
        if len(series) >= 2:
            delta = series[-1].get("y", 0) - series[0].get("y", 0)
        elif len(series) == 1:
            delta = series[0].get("y", 0)
        else:
            delta = 0

        current_points = series[-1].get("y", 0) if series else 0

        # Time tracked (first to last entry)
        if len(series) >= 2:
            t_start = series[0].get("x", 0) / 1000
            t_end = series[-1].get("x", 0) / 1000
            hours = (t_end - t_start) / 3600
        else:
            hours = 0

        total_time_tracked += hours

        # Prediction stats — prefer SQLite data, fall back to JSON
        name_lower = name.lower()
        sqlite_st = sqlite_pred_stats.get(name) or sqlite_pred_stats.get(name_lower)
        if sqlite_st and sqlite_st.get("total", 0) > 0:
            s_wins = sqlite_st["wins"]
            s_losses = sqlite_st["losses"]
            s_refunds = sqlite_st["refunds"]
            s_net = sqlite_st["net_points"]
        else:
            # Fallback: JSON dry_run_predictions
            s_wins = 0
            s_losses = 0
            s_refunds = 0
            s_net = 0
            for pred in predictions:
                active_strat = pred.get("active_strategy", "")
                for strat in pred.get("strategies", []):
                    if strat.get("strategy") == active_strat:
                        rt = strat.get("result_type", "")
                        pts = strat.get("points_gained", 0)
                        if rt == "WIN":
                            s_wins += 1
                        elif rt == "LOSE":
                            s_losses += 1
                        elif rt == "REFUND":
                            s_refunds += 1
                        s_net += pts
                        break

        total_points_earned += delta
        total_predictions += s_wins + s_losses + s_refunds
        total_wins += s_wins
        total_losses += s_losses
        total_refunds += s_refunds
        net_prediction_points += s_net

        streamer_stats.append({
            "name": name,
            "current_points": current_points,
            "points_delta": delta,
            "predictions": s_wins + s_losses + s_refunds,
            "wins": s_wins,
            "losses": s_losses,
            "win_rate": round(s_wins / max(s_wins + s_losses, 1) * 100, 1),
            "net_prediction_points": s_net,
            "hours_tracked": round(hours, 1),
        })

    # Sort by points delta descending
    streamer_stats.sort(key=lambda x: x["points_delta"], reverse=True)

    total_current_points = sum(s["current_points"] for s in streamer_stats)

    result = {
        "total_streamers": len(available),
        "streamer_count": len(available),
        "total_points_earned": total_points_earned,
        "total_points_gained": total_points_earned,
        "total_current_points": total_current_points,
        "total_predictions": total_predictions,
        "total_wins": total_wins,
        "total_losses": total_losses,
        "total_refunds": total_refunds,
        "overall_win_rate": round(total_wins / max(total_wins + total_losses, 1) * 100, 1),
        "net_prediction_points": net_prediction_points,
        "total_hours_tracked": round(total_time_tracked, 1),
        "most_profitable": streamer_stats[0]["name"] if streamer_stats else None,
        "streamers": streamer_stats,
        "telemetry_available": _telemetry is not None,
    }

    return Response(
        json.dumps(result),
        status=200,
        mimetype="application/json",
    )


# === LOG-BASED BACKIMPORT === #

# Compiled regex patterns for parsing miner log lines
_LOG_TS_RE = re.compile(
    r"^(\d{2}/\d{2}/\d{2,4}\s+\d{2}:\d{2}:\d{2})\s*-\s*(\w+)\s*-\s*"
)
_LOG_POINTS_GAIN_RE = re.compile(
    r"\+(\d+)\s*(?:→|-->)\s*Streamer\(username=(\w+)[^)]*\)\s*-\s*Reason:\s*(\w+)",
    re.IGNORECASE,
)
_LOG_ONLINE_RE = re.compile(
    r"Streamer\(username=(\w+)[^)]*\)\s+is\s+Online!",
    re.IGNORECASE,
)
_LOG_OFFLINE_RE = re.compile(
    r"Streamer\(username=(\w+)[^)]*\)\s+is\s+Offline!",
    re.IGNORECASE,
)
_LOG_PREDICTION_RESULT_RE = re.compile(
    r"EventPrediction.*?username=(\w+).*?title=(.+?)\)"
    r"\s*-\s*Decision:\s*(\d+):\s*(.+?)\s*\((\w+)\)\s*-\s*Result:\s*(.+)",
    re.IGNORECASE,
)
_LOG_BET_PLACE_RE = re.compile(
    r"Place\s+([\d.]+[kKMB]?)\s+channel points on:\s+(.+?)(?:\s*\(confidence:\s*([\d.]+)\))?$",
    re.IGNORECASE,
)
_LOG_RAID_RE = re.compile(
    r"Joining raid from\s+Streamer\(username=(\w+)[^)]*\)\s+to\s+(\w+)",
    re.IGNORECASE,
)
_LOG_BONUS_RE = re.compile(
    r"Claiming the bonus for\s+Streamer\(username=(\w+)",
    re.IGNORECASE,
)


def _parse_log_timestamp(ts_str: str) -> str | None:
    """Parse DD/MM/YY HH:MM:SS or DD/MM/YYYY HH:MM:SS to ISO format."""
    for fmt in ("%d/%m/%y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            dt = datetime.strptime(ts_str, fmt)
            return dt.isoformat()
        except ValueError:
            continue
    return None


def _parse_log_line(line: str) -> dict | None:
    """Parse a single log line into a structured event dict."""
    # Fast pre-filter: skip lines that can't match any of our patterns
    if not any(kw in line for kw in (
        "Streamer(username=", "Place ", "Joining raid",
        "is Online!", "is Offline!", "Claiming the bonus",
        "Decision:", "Reason:",
    )):
        return None

    ts_match = _LOG_TS_RE.match(line)
    if not ts_match:
        return None
    timestamp = _parse_log_timestamp(ts_match.group(1))
    level = ts_match.group(2)
    msg = line[ts_match.end():]

    # Points gained: +N → Streamer(username=X) - Reason: Y
    m = _LOG_POINTS_GAIN_RE.search(msg)
    if m:
        return {
            "type": "points_gain",
            "timestamp": timestamp,
            "amount": int(m.group(1)),
            "streamer": m.group(2),
            "reason": m.group(3),
        }

    # Prediction result
    m = _LOG_PREDICTION_RESULT_RE.search(msg)
    if m:
        return {
            "type": "prediction_result",
            "timestamp": timestamp,
            "streamer": m.group(1),
            "title": m.group(2).strip(),
            "choice_index": int(m.group(3)),
            "choice_title": m.group(4).strip(),
            "choice_color": m.group(5),
            "result_detail": m.group(6).strip(),
        }

    # Bet placement
    m = _LOG_BET_PLACE_RE.search(msg)
    if m:
        return {
            "type": "bet_placed",
            "timestamp": timestamp,
            "amount": m.group(1),
            "outcome": m.group(2).strip(),
            "confidence": m.group(3),
        }

    # Raid
    m = _LOG_RAID_RE.search(msg)
    if m:
        return {
            "type": "raid",
            "timestamp": timestamp,
            "from_streamer": m.group(1),
            "to_streamer": m.group(2),
        }

    # Online
    m = _LOG_ONLINE_RE.search(msg)
    if m:
        return {
            "type": "streamer_online",
            "timestamp": timestamp,
            "streamer": m.group(1),
        }

    # Offline
    m = _LOG_OFFLINE_RE.search(msg)
    if m:
        return {
            "type": "streamer_offline",
            "timestamp": timestamp,
            "streamer": m.group(1),
        }

    # Bonus claim
    m = _LOG_BONUS_RE.search(msg)
    if m:
        return {
            "type": "bonus_claim",
            "timestamp": timestamp,
            "streamer": m.group(1),
        }

    return None


def log_backimport():
    """Parse miner log files and return structured event data.
    Supports filtering by streamer, event type, and date range.
    Optional: forward parsed events to Discord as embeds.
    Reads files newest-first and streams in reverse for fast results."""
    logs_path = os.path.join(Path().absolute(), "logs")
    if not os.path.isdir(logs_path):
        return Response(
            json.dumps({"error": "Logs directory not found."}),
            status=404,
            mimetype="application/json",
        )

    # Query params
    streamer_filter = request.args.get("streamer", "").lower()
    event_filter = request.args.get("type", "")  # comma-separated event types
    limit = request.args.get("limit", 200, type=int)
    limit = min(limit, 5000)  # hard cap
    send_discord = request.args.get("discord", "false").lower() == "true"
    include_rotated = request.args.get("all_files", "false").lower() == "true"

    event_types = set(event_filter.split(",")) if event_filter else set()

    # Find log files — only current by default, all if requested
    all_log_files = sorted(
        [
            os.path.join(logs_path, f)
            for f in os.listdir(logs_path)
            if f.endswith(".log") or ".log." in f
        ],
        key=os.path.getmtime,
        reverse=True,
    )
    if include_rotated:
        log_files = all_log_files
    else:
        # Only the most recent (current) log file
        log_files = all_log_files[:1]

    events = []
    for log_file in log_files:
        if len(events) >= limit:
            break
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            for line in reversed(lines):
                parsed = _parse_log_line(line.strip())
                if parsed is None:
                    continue
                # Filter by streamer
                streamer_name = (
                    parsed.get("streamer")
                    or parsed.get("from_streamer")
                    or ""
                ).lower()
                if streamer_filter and streamer_name != streamer_filter:
                    continue
                # Filter by event type
                if event_types and parsed["type"] not in event_types:
                    continue
                events.append(parsed)
                if len(events) >= limit:
                    break
        except Exception as e:
            logger.warning(f"Error reading log file {log_file}: {e}")

    # Optionally send to Discord
    discord_sent = 0
    if send_discord and events:
        from TwitchChannelPointsMiner.classes.Discord import EVENT_COLORS

        discord = _get_discord()
        if discord:
            _TYPE_TO_EVENT = {
                "points_gain": lambda e: f"GAIN_FOR_{e.get('reason', 'WATCH').upper()}",
                "prediction_result": lambda e: "BET_WIN" if "WIN" in e.get("result_detail", "").upper() else ("BET_LOSE" if "LOSE" in e.get("result_detail", "").upper() else "BET_REFUND"),
                "bet_placed": lambda _: "BET_START",
                "raid": lambda _: "JOIN_RAID",
                "streamer_online": lambda _: "STREAMER_ONLINE",
                "streamer_offline": lambda _: "STREAMER_OFFLINE",
                "bonus_claim": lambda _: "BONUS_CLAIM",
            }
            for ev in events:
                event_str_fn = _TYPE_TO_EVENT.get(ev["type"])
                if not event_str_fn:
                    continue
                event_str = event_str_fn(ev)
                if event_str not in EVENT_COLORS:
                    event_str = "GAIN_FOR_WATCH"

                # Build description
                if ev["type"] == "points_gain":
                    desc = f"+{ev['amount']} points — Reason: {ev['reason']}"
                elif ev["type"] == "prediction_result":
                    desc = f"**{ev['title']}**\nDecision: {ev['choice_title']} ({ev['choice_color']})\nResult: {ev['result_detail']}"
                elif ev["type"] == "bet_placed":
                    desc = f"Placed **{ev['amount']}** on: {ev['outcome']}"
                    if ev.get("confidence"):
                        desc += f" (confidence: {ev['confidence']})"
                elif ev["type"] == "raid":
                    desc = f"Joining raid from **{ev['from_streamer']}** to **{ev['to_streamer']}**"
                elif ev["type"] in ("streamer_online", "streamer_offline"):
                    status = "Online" if ev["type"] == "streamer_online" else "Offline"
                    desc = f"**{ev['streamer']}** is {status}!"
                elif ev["type"] == "bonus_claim":
                    desc = f"Claiming bonus for **{ev['streamer']}**"
                else:
                    continue

                ch = ev.get("streamer") or ev.get("from_streamer")
                embed = discord._build_embed(desc, event_str, ch)
                if ev.get("timestamp"):
                    embed["timestamp"] = ev["timestamp"]
                embed["footer"]["text"] += " • 📥 Log Import"

                payload = {
                    "username": "Twitch Channel Points Miner",
                    "avatar_url": "https://i.imgur.com/X9fEkhT.png",
                    "embeds": [embed],
                }
                discord._rate_limiter.acquire()
                try:
                    import requests as req
                    resp = req.post(discord.webhook_api, json=payload, timeout=10)
                    if resp.status_code in (200, 204):
                        discord._rate_limiter.report_success()
                        discord_sent += 1
                    elif resp.status_code == 429:
                        discord._rate_limiter.report_rate_limited()
                except Exception:
                    logger.warning("Failed to send log-imported event to Discord", exc_info=True)

    return Response(
        json.dumps({
            "total_events": len(events),
            "discord_sent": discord_sent,
            "events": events,
        }),
        status=200,
        mimetype="application/json",
    )


def _get_discord():
    """Get a Discord instance from runtime Settings or from settings.json."""
    from TwitchChannelPointsMiner.classes.Settings import Settings as MinerSettings

    discord = getattr(getattr(MinerSettings, "logger", None), "discord", None)
    if discord is not None:
        return discord

    # Fallback: construct from settings.json
    settings_path = os.path.join(Path().absolute(), "settings.json")
    if not os.path.isfile(settings_path):
        return None
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        dc_cfg = cfg.get("discord") or cfg.get("logger", {}).get("discord")
        if not dc_cfg or not dc_cfg.get("webhook_api"):
            return None
        from TwitchChannelPointsMiner.classes.Discord import Discord
        return Discord(
            webhook_api=dc_cfg["webhook_api"],
            events=[],  # not needed for backimport
            bot_token=dc_cfg.get("bot_token"),
            channel_id=dc_cfg.get("channel_id"),
        )
    except Exception:
        logger.debug("Could not construct Discord from settings.json", exc_info=True)
        return None


def discord_backimport():
    """Trigger Discord back-import: fetch old messages, parse, and re-post as embeds."""
    discord = _get_discord()
    if discord is None:
        return Response(
            json.dumps({"error": "Discord is not configured."}),
            status=400,
            mimetype="application/json",
        )

    try:
        limit = request.args.get("limit", 100, type=int)
        cleanup = request.args.get("cleanup", "false").lower() == "true"

        old_messages = discord.fetch_old_messages(limit=min(limit, 100))
        if not old_messages:
            return Response(
                json.dumps({"imported": 0, "migrated": 0, "message": "No parseable old messages found."}),
                status=200,
                mimetype="application/json",
            )

        migrated = 0
        if cleanup:
            migrated = discord.cleanup_and_repost(old_messages)

        return Response(
            json.dumps({
                "imported": len(old_messages),
                "migrated": migrated,
                "events": old_messages,
            }),
            status=200,
            mimetype="application/json",
        )
    except Exception as e:
        logger.error(f"Discord back-import failed: {e}", exc_info=True)
        return Response(
            json.dumps({"error": str(e)}),
            status=500,
            mimetype="application/json",
        )


def check_assets():
    required_files = [
        "banner.png",
        "charts.html",
        "config_editor.html",
        "script.js",
        "style.css",
        "dark-theme.css",
    ]
    assets_folder = os.path.join(Path().absolute(), "assets")
    if os.path.isdir(assets_folder) is False:
        logger.info(f"Assets folder not found at {assets_folder}")
        download_assets(assets_folder, required_files)
    else:
        for f in required_files:
            if os.path.isfile(os.path.join(assets_folder, f)) is False:
                logger.info(f"Missing file {f} in {assets_folder}")
                download_assets(assets_folder, required_files)
                break


# === TELEMETRY / SQLITE ENDPOINTS === #

def telemetry_import():
    """Import all log files into the SQLite telemetry database,
    then backfill dry-run strategy simulations for historical predictions."""
    global _telemetry
    if _telemetry is None:
        _telemetry = Telemetry()

    logs_path = os.path.join(Path().absolute(), "logs")
    if not os.path.isdir(logs_path):
        return Response(
            json.dumps({"error": "Logs directory not found."}),
            status=404,
            mimetype="application/json",
        )

    results = _telemetry.import_all_logs(logs_path, _parse_log_line)

    # Backfill dry-run strategy simulations for imported predictions
    backfilled = _telemetry.backfill_dry_run_from_predictions()

    summary = _telemetry.get_db_summary()
    return Response(
        json.dumps({
            "imported_files": results,
            "dry_run_backfilled": backfilled,
            "db_summary": summary,
        }),
        status=200,
        mimetype="application/json",
    )


def telemetry_stats():
    """Return prediction stats from the SQLite telemetry database."""
    global _telemetry
    if _telemetry is None:
        _telemetry = Telemetry()

    streamer = request.args.get("streamer", "")
    if streamer:
        stats = _telemetry.get_prediction_stats(streamer)
    else:
        stats = _telemetry.get_prediction_stats()

    return Response(
        json.dumps(stats),
        status=200,
        mimetype="application/json",
    )


def telemetry_predictions():
    """Return recent predictions from SQLite."""
    global _telemetry
    if _telemetry is None:
        _telemetry = Telemetry()

    streamer = request.args.get("streamer", "")
    limit = request.args.get("limit", 50, type=int)
    limit = min(limit, 500)

    preds = _telemetry.get_recent_predictions(limit=limit, streamer=streamer or None)
    return Response(
        json.dumps(preds),
        status=200,
        mimetype="application/json",
    )


def telemetry_summary():
    """Return a summary of the telemetry database."""
    global _telemetry
    if _telemetry is None:
        _telemetry = Telemetry()

    summary = _telemetry.get_db_summary()
    event_counts = _telemetry.get_event_counts()
    summary["event_breakdown"] = event_counts
    return Response(
        json.dumps(summary),
        status=200,
        mimetype="application/json",
    )


def config_reload():
    """Hot-reload the settings.json configuration at runtime."""
    settings_path = os.path.join(Path().absolute(), "settings.json")
    if not os.path.isfile(settings_path):
        return Response(
            json.dumps({"success": False, "error": "No settings.json found."}),
            status=404,
            mimetype="application/json",
        )
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # Apply bet settings if present
        bet = cfg.get("bet", {})
        if bet:
            if hasattr(Settings, "bet"):
                for k, v in bet.items():
                    if hasattr(Settings.bet, k):
                        setattr(Settings.bet, k, v)
        logger.info("Config hot-reloaded from settings.json via API")
        return Response(
            json.dumps({"success": True, "message": "Config reloaded."}),
            status=200,
            mimetype="application/json",
        )
    except Exception as e:
        logger.error(f"Config reload failed: {e}")
        return Response(
            json.dumps({"success": False, "error": str(e)}),
            status=500,
            mimetype="application/json",
        )


def _read_settings_json():
    """Read settings.json and return (cfg_dict, path) or (None, path)."""
    settings_path = os.path.join(Path().absolute(), "settings.json")
    if not os.path.isfile(settings_path):
        return None, settings_path
    with open(settings_path, "r", encoding="utf-8") as f:
        return json.load(f), settings_path


def _get_channel_strategy(cfg, streamer_name):
    """Return the strategy configured for a specific streamer, falling back to global."""
    # Check per-streamer override first
    for entry in cfg.get("streamers", []):
        if isinstance(entry, dict) and entry.get("username", "").lower() == streamer_name.lower():
            strat = (entry.get("settings") or {}).get("bet", {}).get("strategy")
            if strat:
                return strat.upper()
    # Fall back to global
    return (cfg.get("streamer_settings", {}).get("bet", {}).get("strategy", "")).upper()


def _set_channel_strategy(cfg, streamer_name, new_strategy):
    """Set the strategy for a specific streamer in-place. Creates entry if needed."""
    # Find or create streamer entry
    for entry in cfg.get("streamers", []):
        if isinstance(entry, dict) and entry.get("username", "").lower() == streamer_name.lower():
            if "settings" not in entry:
                entry["settings"] = {}
            if "bet" not in entry["settings"]:
                entry["settings"]["bet"] = {}
            entry["settings"]["bet"]["strategy"] = new_strategy
            return
    # Streamer not in list — add with override
    if "streamers" not in cfg:
        cfg["streamers"] = []
    cfg["streamers"].append({
        "username": streamer_name,
        "settings": {"bet": {"strategy": new_strategy}},
    })


def strategy_switch():
    """Switch the active betting strategy per channel or globally."""
    cfg, settings_path = _read_settings_json()
    if cfg is None:
        return Response(
            json.dumps({"error": "No settings.json found."}),
            status=404,
            mimetype="application/json",
        )

    data = request.get_json(silent=True) or {}
    new_strategy = data.get("strategy", "").upper()
    streamer_name = data.get("streamer", "").strip()

    valid_strategies = [
        "MOST_VOTED", "HIGH_ODDS", "PERCENTAGE", "SMART_MONEY",
        "SMART", "HISTORICAL", "KELLY_CRITERION", "CONTRARIAN",
        "MOMENTUM", "VALUE_BET", "WEIGHTED_AVERAGE", "UNDERDOG",
    ]

    if new_strategy not in valid_strategies:
        return Response(
            json.dumps({
                "error": f"Invalid strategy '{new_strategy}'.",
                "valid": valid_strategies,
            }),
            status=400,
            mimetype="application/json",
        )

    try:
        if streamer_name:
            old_strategy = _get_channel_strategy(cfg, streamer_name)
            _set_channel_strategy(cfg, streamer_name, new_strategy)
            scope = streamer_name
        else:
            old_strategy = (cfg.get("streamer_settings", {}).get("bet", {}).get("strategy", ""))
            if "streamer_settings" not in cfg:
                cfg["streamer_settings"] = {}
            if "bet" not in cfg["streamer_settings"]:
                cfg["streamer_settings"]["bet"] = {}
            cfg["streamer_settings"]["bet"]["strategy"] = new_strategy
            scope = "global"

        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)

        # Record the switch in telemetry
        global _telemetry
        if _telemetry is None:
            _telemetry = Telemetry()
        try:
            _telemetry.record_strategy_switch(
                streamer_name or "__global__",
                old_strategy, new_strategy, reason="manual",
            )
        except Exception:
            pass

        logger.info(f"Strategy switched for {scope}: {old_strategy} → {new_strategy}")
        return Response(
            json.dumps({
                "success": True,
                "streamer": streamer_name or None,
                "old_strategy": old_strategy,
                "new_strategy": new_strategy,
                "message": f"Strategy for {scope} changed to {new_strategy}.",
            }),
            status=200,
            mimetype="application/json",
        )
    except Exception as e:
        return Response(
            json.dumps({"error": str(e)}),
            status=500,
            mimetype="application/json",
        )


def strategy_switch_all():
    """Apply the best-performing strategy to all channels or a specific set."""
    cfg, settings_path = _read_settings_json()
    if cfg is None:
        return Response(
            json.dumps({"error": "No settings.json found."}),
            status=404,
            mimetype="application/json",
        )

    data = request.get_json(silent=True) or {}
    target_strategy = data.get("strategy", "").upper()

    valid_strategies = [
        "MOST_VOTED", "HIGH_ODDS", "PERCENTAGE", "SMART_MONEY",
        "SMART", "HISTORICAL", "KELLY_CRITERION", "CONTRARIAN",
        "MOMENTUM", "VALUE_BET", "WEIGHTED_AVERAGE", "UNDERDOG",
    ]

    if target_strategy and target_strategy not in valid_strategies:
        return Response(
            json.dumps({"error": f"Invalid strategy '{target_strategy}'."}),
            status=400,
            mimetype="application/json",
        )

    global _telemetry
    if _telemetry is None:
        _telemetry = Telemetry()

    try:
        # Get list of streamers with dry-run data
        path = Settings.analytics_path
        streamer_names = [
            f.replace(".json", "")
            for f in os.listdir(path)
            if f.endswith(".json") and os.path.isfile(os.path.join(path, f))
        ]

        switched = []
        for sname in streamer_names:
            if target_strategy:
                best = target_strategy
            else:
                best = _telemetry.get_best_strategy(sname)
            if not best or best == "ACTIVE":
                continue

            old = _get_channel_strategy(cfg, sname)
            if old.upper() == best.upper():
                continue

            _set_channel_strategy(cfg, sname, best)
            switched.append({"streamer": sname, "old": old, "new": best})
            try:
                _telemetry.record_strategy_switch(sname, old, best, reason="apply_best")
            except Exception:
                pass

        # Also update global default if a strategy was specified
        if target_strategy:
            old_global = cfg.get("streamer_settings", {}).get("bet", {}).get("strategy", "")
            if "streamer_settings" not in cfg:
                cfg["streamer_settings"] = {}
            if "bet" not in cfg["streamer_settings"]:
                cfg["streamer_settings"]["bet"] = {}
            cfg["streamer_settings"]["bet"]["strategy"] = target_strategy

        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)

        return Response(
            json.dumps({"success": True, "switched": switched, "count": len(switched)}),
            status=200,
            mimetype="application/json",
        )
    except Exception as e:
        return Response(
            json.dumps({"error": str(e)}),
            status=500,
            mimetype="application/json",
        )


def auto_adjust_config():
    """GET/POST auto-adjust strategy settings."""
    cfg, settings_path = _read_settings_json()
    if cfg is None:
        return Response(
            json.dumps({"error": "No settings.json found."}),
            status=404,
            mimetype="application/json",
        )

    if request.method == "GET":
        aa = cfg.get("auto_adjust", {})
        return Response(
            json.dumps({
                "enabled": aa.get("enabled", False),
                "threshold": aa.get("threshold", 3),
                "min_predictions": aa.get("min_predictions", 5),
            }),
            status=200,
            mimetype="application/json",
        )

    # POST — update
    data = request.get_json(silent=True) or {}
    if "auto_adjust" not in cfg:
        cfg["auto_adjust"] = {}
    if "enabled" in data:
        cfg["auto_adjust"]["enabled"] = bool(data["enabled"])
    if "threshold" in data:
        cfg["auto_adjust"]["threshold"] = max(1, int(data["threshold"]))
    if "min_predictions" in data:
        cfg["auto_adjust"]["min_predictions"] = max(1, int(data["min_predictions"]))

    try:
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
        return Response(
            json.dumps({"success": True, "auto_adjust": cfg["auto_adjust"]}),
            status=200,
            mimetype="application/json",
        )
    except Exception as e:
        return Response(
            json.dumps({"error": str(e)}),
            status=500,
            mimetype="application/json",
        )


def _parse_gained(val):
    """Parse a gained string like '+1.5k' or '-500' to int."""
    if not val:
        return 0
    try:
        s = str(val).replace(",", "").strip()
        mult = 1
        if s.lower().endswith("k"):
            mult = 1000
            s = s[:-1]
        return int(float(s) * mult)
    except (ValueError, TypeError):
        return 0


def discord_summary():
    """Send a summary report to Discord with per-streamer stats."""
    discord = _get_discord()
    if discord is None:
        return Response(
            json.dumps({"error": "Discord is not configured."}),
            status=400,
            mimetype="application/json",
        )

    global _telemetry
    if _telemetry is None:
        _telemetry = Telemetry()

    # Gather stats
    streamer_stats = _telemetry.get_streamer_prediction_stats()
    overall = _telemetry.get_prediction_stats()
    db_info = _telemetry.get_db_summary()

    # Build main summary embed
    total_str = f"**{overall.get('total', 0)}** predictions"
    win_str = f"✅ {overall.get('wins', 0)}W / ❌ {overall.get('losses', 0)}L / 🔄 {overall.get('refunds', 0)}R"
    rate_str = f"**{overall.get('win_rate', 0)}%** win rate"
    net = overall.get('net_points', 0)
    net_str = f"{'🟢 +' if net >= 0 else '🔴 '}{net:,} points net"

    desc_lines = [
        f"📊 {total_str}",
        f"{win_str}",
        f"🎯 {rate_str}",
        f"💰 {net_str}",
        "",
        "**Per-Streamer Breakdown:**",
    ]

    # Sort streamers by net_points
    sorted_streamers = sorted(
        streamer_stats.items(),
        key=lambda x: x[1].get("net_points", 0),
        reverse=True,
    )

    for sname, st in sorted_streamers[:15]:  # Discord embed field limit
        s_net = st.get("net_points", 0)
        prefix = "+" if s_net >= 0 else ""
        desc_lines.append(
            f"**{sname}** — {st.get('wins', 0)}W/{st.get('losses', 0)}L "
            f"({st.get('win_rate', 0)}%) • {prefix}{s_net:,}pts"
        )

    description = "\n".join(desc_lines)
    embed = discord._build_embed(description, "BET_GENERAL", None)
    embed["title"] = "📋 Prediction Summary Report"
    embed["footer"]["text"] = f"📂 Summary • {db_info.get('predictions', 0)} total predictions"

    payload = {
        "username": "Twitch Channel Points Miner",
        "avatar_url": AVATAR_URL,
        "embeds": [embed],
    }

    sent = 0
    try:
        discord._rate_limiter.acquire()
        resp = requests.post(discord.webhook_api, json=payload, timeout=10)
        if resp.status_code in (200, 204):
            discord._rate_limiter.report_success()
            sent = 1
        elif resp.status_code == 429:
            discord._rate_limiter.report_rate_limited()
    except Exception:
        logger.warning("Failed to send Discord summary", exc_info=True)

    # Send per-streamer detail embeds (top 5)
    for sname, st in sorted_streamers[:5]:
        s_net = st.get("net_points", 0)
        prefix = "+" if s_net >= 0 else ""
        s_desc = (
            f"✅ **{st.get('wins', 0)}** wins / ❌ **{st.get('losses', 0)}** losses"
            f" / 🔄 **{st.get('refunds', 0)}** refunds\n"
            f"🎯 Win Rate: **{st.get('win_rate', 0)}%**\n"
            f"💰 Net: **{prefix}{s_net:,}** points"
        )
        event_str = "BET_WIN" if s_net >= 0 else "BET_LOSE"
        s_embed = discord._build_embed(s_desc, event_str, sname)
        s_embed["title"] = f"📺 {sname} — Prediction Stats"

        s_payload = {
            "username": "Twitch Channel Points Miner",
            "avatar_url": AVATAR_URL,
            "embeds": [s_embed],
        }
        try:
            discord._rate_limiter.acquire()
            resp = requests.post(discord.webhook_api, json=s_payload, timeout=10)
            if resp.status_code in (200, 204):
                discord._rate_limiter.report_success()
                sent += 1
            elif resp.status_code == 429:
                discord._rate_limiter.report_rate_limited()
        except Exception:
            logger.warning(f"Failed to send Discord embed for {sname}", exc_info=True)

    return Response(
        json.dumps({"sent": sent, "streamers": len(sorted_streamers)}),
        status=200,
        mimetype="application/json",
    )


def discord_cleanup():
    """Fetch old messages, group by streamer, and migrate to organized embeds.
    Backs up parsed message data to telemetry DB before deleting.
    Supports pagination and filtering."""
    discord = _get_discord()
    if discord is None:
        return Response(
            json.dumps({"error": "Discord is not configured."}),
            status=400,
            mimetype="application/json",
        )

    try:
        global _telemetry
        limit = request.args.get("limit", 100, type=int)
        do_cleanup = request.args.get("cleanup", "false").lower() == "true"
        purge_mode = request.args.get("purge", "false").lower() == "true"
        rebuild = request.args.get("rebuild", "true").lower() == "true"
        streamer_filter = request.args.get("streamer", "").lower()
        event_filter = request.args.get("type", "")  # comma-separated

        event_types = set(event_filter.split(",")) if event_filter else set()

        if purge_mode:
            if _telemetry is None:
                _telemetry = Telemetry()
            deleted = discord.purge_all_messages(limit=min(limit, 500))

            # Invalidate stored logbook message IDs — the messages are gone
            state_path = os.path.join(Settings.analytics_path, "logbook_state.json")
            if os.path.isfile(state_path):
                os.remove(state_path)

            sent = 0
            names = []
            if rebuild:
                streamers = _telemetry.get_all_streamers()
                for sname in streamers:
                    try:
                        payload, _ = _build_logbook_payload(sname, _telemetry, limit=50)
                        msg_id = discord.upsert_logbook_embed(sname, payload, state_path)
                        if msg_id:
                            sent += 1
                            names.append(sname)
                    except Exception:
                        logger.warning("purge rebuild: failed for %s", sname, exc_info=True)

            return Response(
                json.dumps({"purged": deleted, "logbooks_sent": sent, "streamers": names}),
                status=200,
                mimetype="application/json",
            )

        old_messages = discord.fetch_old_messages(limit=min(limit, 500))
        if not old_messages:
            return Response(
                json.dumps({"total": 0, "groups": {}, "migrated": 0, "backed_up": 0}),
                status=200,
                mimetype="application/json",
            )

        # Filter
        filtered = []
        for msg in old_messages:
            s = (msg.get("streamer") or msg.get("from_streamer") or "").lower()
            if streamer_filter and s != streamer_filter:
                continue
            if event_types and msg.get("type") not in event_types:
                continue
            filtered.append(msg)

        # Group by streamer
        groups = {}
        for msg in filtered:
            s = msg.get("streamer") or msg.get("from_streamer") or "unknown"
            if s not in groups:
                groups[s] = []
            groups[s].append(msg)

        # === Backup to telemetry DB before cleanup ===
        backed_up = 0
        if do_cleanup:
            if _telemetry is None:
                _telemetry = Telemetry()
            for msg in filtered:
                try:
                    mtype = msg.get("type")
                    ts = msg.get("timestamp") or datetime.now().isoformat()
                    streamer_name = msg.get("streamer") or msg.get("from_streamer")
                    if mtype == "prediction_result":
                        _telemetry.record_prediction(
                            timestamp=ts,
                            streamer=streamer_name or "",
                            title=msg.get("title"),
                            event_id=msg.get("event_id"),
                            choice_index=msg.get("choice_index"),
                            choice_title=msg.get("choice_title"),
                            choice_color=msg.get("choice_color"),
                            amount_placed=0,
                            result=msg.get("result"),
                            points_gained=_parse_gained(msg.get("gained", "0")),
                            source="discord_backup",
                        )
                    elif mtype == "points_gain":
                        _telemetry.record_points(
                            timestamp=ts,
                            streamer=streamer_name or "",
                            amount=msg.get("amount", 0),
                            reason=msg.get("reason"),
                            source="discord_backup",
                        )
                    # Always save as generic event
                    _telemetry.record_event(
                        event_type=mtype or "unknown",
                        streamer=streamer_name,
                        data=msg,
                        timestamp=ts,
                        source="discord_backup",
                    )
                    backed_up += 1
                except Exception:
                    logger.debug("Failed to backup Discord message", exc_info=True)
            logger.info("Backed up %d messages to telemetry DB before cleanup", backed_up)

        migrated = 0
        if do_cleanup:
            migrated = discord.cleanup_and_repost(filtered)
            # After cleanup, send grouped summary embeds per streamer
            for s, msgs in groups.items():
                pred_count = sum(1 for m in msgs if m.get("type") == "prediction_result")
                raid_count = sum(1 for m in msgs if m.get("type") == "raid")
                gain_count = sum(1 for m in msgs if m.get("type") == "points_gain")
                bet_count = sum(1 for m in msgs if m.get("type") == "bet_placed")
                lines = [f"**{s}** — Migrated {len(msgs)} messages"]
                if pred_count:
                    lines.append(f"🎰 {pred_count} prediction(s)")
                if raid_count:
                    lines.append(f"⚔️ {raid_count} raid(s)")
                if gain_count:
                    lines.append(f"💰 {gain_count} point gain(s)")
                if bet_count:
                    lines.append(f"🎲 {bet_count} bet(s) placed")
                desc = "\n".join(lines)
                embed = discord._build_embed(desc, "BET_GENERAL", s)
                embed["title"] = f"📋 {s} — Migration Summary"
                payload = {
                    "username": "Twitch Channel Points Miner",
                    "avatar_url": AVATAR_URL,
                    "embeds": [embed],
                }
                try:
                    discord._rate_limiter.acquire()
                    requests.post(discord.webhook_api, json=payload, timeout=10)
                except Exception:
                    pass

        # Prepare grouped summary for response
        group_summary = {}
        for s, msgs in groups.items():
            types = {}
            for m in msgs:
                t = m.get("type", "unknown")
                types[t] = types.get(t, 0) + 1
            group_summary[s] = {
                "count": len(msgs),
                "types": types,
            }

        return Response(
            json.dumps({
                "total": len(filtered),
                "groups": group_summary,
                "migrated": migrated,
                "backed_up": backed_up,
                "messages": filtered[:50],  # preview
            }),
            status=200,
            mimetype="application/json",
        )
    except Exception as e:
        logger.error(f"Discord cleanup failed: {e}", exc_info=True)
        return Response(
            json.dumps({"error": str(e)}),
            status=500,
            mimetype="application/json",
        )

def _build_logbook_payload(streamer: str, telemetry, limit: int = 50):
    """Build the Discord logbook embed payload for *streamer*.

    Returns ``(payload_dict, total_events_count)`` so callers need not
    duplicate the embed-formatting logic.
    """
    from TwitchChannelPointsMiner.classes.Discord import EVENT_ICONS

    entries = telemetry.get_channel_log(streamer, limit=limit)

    # Deduplicate consecutive same-type stream status events (e.g. repeated
    # "Stream Offline" entries from reconnect loops polluting the logbook).
    _STREAM_STATUS = {
        "streamer_online": "online",  "STREAMER_ONLINE": "online",
        "streamer_offline": "offline", "STREAMER_OFFLINE": "offline",
    }
    _last_status: str | None = None
    deduped: list = []
    for _e in entries:
        _canonical = _STREAM_STATUS.get(_e.get("event_type", ""))
        if _canonical is not None:
            if _canonical == _last_status:
                continue  # skip consecutive duplicate status event
            _last_status = _canonical
        deduped.append(_e)
    entries = deduped

    wins = losses = 0
    net_pts = 0
    lines = []

    for entry in entries[:25]:
        etype = entry.get("event_type", "")
        icon = EVENT_ICONS.get(etype, "📌")
        ts = entry.get("timestamp", "")
        time_str = ts[11:16] if len(ts) >= 16 else (ts[:10] if ts else "?")
        data = entry.get("data") or {}

        if entry["category"] == "prediction":
            result = (data.get("result") or "").upper()
            pts = int(data.get("points_gained") or 0)
            if result == "WIN":
                wins += 1
                result_icon = "✅"
            elif result == "LOSE":
                losses += 1
                result_icon = "❌"
            else:
                result_icon = "🔄"
            net_pts += pts
            pts_str = f"+{pts:,}" if pts >= 0 else f"{pts:,}"
            title_short = (data.get("title") or "Prediction")[:32]
            strategy = data.get("strategy") or ""
            strat_tag = f" `{strategy}`" if strategy else ""
            line = (
                f"`{time_str}` {result_icon} {title_short}"
                f" ({pts_str}pts){strat_tag}"
            )
        elif entry["category"] == "points":
            amt = int(data.get("amount") or 0)
            reason = (data.get("reason") or "bonus")[:24]
            line = f"`{time_str}` {icon} +{amt:,}pts — {reason}"
        else:
            if etype in ("streamer_online", "STREAMER_ONLINE"):
                line = f"`{time_str}` 🟢 Stream **Online**"
            elif etype in ("streamer_offline", "STREAMER_OFFLINE"):
                line = f"`{time_str}` 🔴 Stream **Offline**"
            elif etype in ("raid", "JOIN_RAID"):
                to = data.get("to_streamer", "?") if isinstance(data, dict) else "?"
                line = f"`{time_str}` 📡 Raid → **{to}**"
            elif etype in ("bonus_claim", "BONUS_CLAIM"):
                line = f"`{time_str}` 🎁 Bonus claimed"
            elif etype in ("watch_streak", "WATCH_STREAK"):
                pts = int(data.get("points") or 0)
                line = f"`{time_str}` 🔥 Watch streak +{pts:,}pts"
            else:
                line = f"`{time_str}` {icon} {etype}"

        lines.append(line)

    if not lines:
        lines = ["*No events recorded yet.*"]
    elif len(entries) > 25:
        lines.append(f"*…{len(entries) - 25} older events not shown*")

    total = wins + losses
    win_rate = f"{100 * wins / total:.1f}%" if total > 0 else "N/A"
    net_str = f"+{net_pts:,}" if net_pts >= 0 else f"{net_pts:,}"
    now_str = datetime.utcnow().strftime("%d %b %H:%M UTC")

    embed = {
        "title": f"📖 {streamer} — Event Logbook",
        "description": "\n".join(lines),
        "color": 0x9146FF,
        "footer": {
            "text": (
                f"📊 {wins}W / {losses}L  ({win_rate})  |  "
                f"Net: {net_str}pts  |  Updated: {now_str}"
            )
        },
    }
    payload = {
        "username": "Twitch Channel Points Miner",
        "avatar_url": AVATAR_URL,
        "embeds": [embed],
    }
    return payload, len(entries)


def discord_channel_log():
    """Send or update a single persistent logbook embed per channel to Discord.

    Each channel gets ONE Discord message that is edited in-place on every call
    so the feed stays clean.  Message IDs are stored in
    ``<analytics_path>/logbook_state.json``.
    """
    discord = _get_discord()
    if discord is None:
        return Response(
            json.dumps({"error": "Discord is not configured."}),
            status=400,
            mimetype="application/json",
        )

    global _telemetry
    if _telemetry is None:
        _telemetry = Telemetry()

    streamer = request.args.get("streamer", "").strip()
    limit = request.args.get("limit", 50, type=int)
    limit = min(limit, 200)

    if not streamer:
        return Response(
            json.dumps({"error": "Missing 'streamer' parameter."}),
            status=400,
            mimetype="application/json",
        )

    state_path = os.path.join(Settings.analytics_path, "logbook_state.json")
    payload, total_events = _build_logbook_payload(streamer, _telemetry, limit=limit)

    msg_id = discord.upsert_logbook_embed(streamer, payload, state_path)
    action = "updated" if (msg_id and os.path.isfile(state_path)) else "created"

    if msg_id:
        return Response(
            json.dumps(
                {
                    "sent": 1,
                    "action": action,
                    "message_id": msg_id,
                    "total_events": total_events,
                }
            ),
            status=200,
            mimetype="application/json",
        )
    return Response(
        json.dumps({"error": "Failed to send or update logbook embed."}),
        status=500,
        mimetype="application/json",
    )


def telemetry_export_db():
    """Download the telemetry.db file directly."""
    global _telemetry
    if _telemetry is None:
        _telemetry = Telemetry()

    db_path = _telemetry.db_path
    if not os.path.isfile(db_path):
        return Response(
            json.dumps({"error": "Telemetry database not found."}),
            status=404,
            mimetype="application/json",
        )

    try:
        with open(db_path, "rb") as f:
            data = f.read()
        return Response(
            data,
            status=200,
            mimetype="application/octet-stream",
            headers={
                "Content-Disposition": "attachment; filename=telemetry.db",
                "Content-Length": str(len(data)),
            },
        )
    except Exception as e:
        return Response(
            json.dumps({"error": str(e)}),
            status=500,
            mimetype="application/json",
        )


def telemetry_export_json():
    """Export all telemetry tables as a JSON download."""
    global _telemetry
    if _telemetry is None:
        _telemetry = Telemetry()

    try:
        tables = _telemetry.export_all_tables()
        content = json.dumps(tables, indent=2, default=str)
        return Response(
            content,
            status=200,
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=telemetry_export.json"},
        )
    except Exception as e:
        return Response(
            json.dumps({"error": str(e)}),
            status=500,
            mimetype="application/json",
        )


def telemetry_import_db():
    """Import telemetry data from an uploaded JSON dump or merge a .db file."""
    global _telemetry
    if _telemetry is None:
        _telemetry = Telemetry()

    content_type = request.content_type or ""

    # JSON import
    if "json" in content_type:
        data = request.get_json(silent=True)
        if not data:
            return Response(
                json.dumps({"error": "No JSON data provided."}),
                status=400,
                mimetype="application/json",
            )
        try:
            imported = _telemetry.import_from_json_dump(data)
            return Response(
                json.dumps({"success": True, "imported": imported}),
                status=200,
                mimetype="application/json",
            )
        except Exception as e:
            return Response(
                json.dumps({"error": str(e)}),
                status=500,
                mimetype="application/json",
            )

    # File upload (multipart)
    if "multipart" in content_type:
        f = request.files.get("file")
        if not f:
            return Response(
                json.dumps({"error": "No file uploaded."}),
                status=400,
                mimetype="application/json",
            )

        # Save to temp, read and import
        import tempfile
        suffix = ".json" if f.filename and f.filename.endswith(".json") else ".db"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            f.save(tmp.name)
            tmp_path = tmp.name

        try:
            if suffix == ".json":
                with open(tmp_path, "r", encoding="utf-8") as jf:
                    data = json.load(jf)
                imported = _telemetry.import_from_json_dump(data)
                os.unlink(tmp_path)
                return Response(
                    json.dumps({"success": True, "imported": imported}),
                    status=200,
                    mimetype="application/json",
                )
            else:
                # .db file — merge by reading from uploaded DB
                src_tele = Telemetry(db_path=tmp_path)
                data = src_tele.export_all_tables()
                imported = _telemetry.import_from_json_dump(data)
                os.unlink(tmp_path)
                return Response(
                    json.dumps({"success": True, "imported": imported}),
                    status=200,
                    mimetype="application/json",
                )
        except Exception as e:
            if os.path.isfile(tmp_path):
                os.unlink(tmp_path)
            return Response(
                json.dumps({"error": str(e)}),
                status=500,
                mimetype="application/json",
            )

    return Response(
        json.dumps({"error": "Unsupported content type. Use JSON or multipart file upload."}),
        status=400,
        mimetype="application/json",
    )


last_sent_log_index = 0

class AnalyticsServer(Thread):
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        refresh: int = 5,
        days_ago: int = 7,
        username: str = None
    ):
        super(AnalyticsServer, self).__init__()

        check_assets()

        self.host = host
        self.port = port
        self.refresh = refresh
        self.days_ago = days_ago
        self.username = username

        def generate_log():
            global last_sent_log_index  # Use the global variable

            # Get the last received log index from the client request parameters
            last_received_index = int(request.args.get("lastIndex", last_sent_log_index))

            logs_path = os.path.join(Path().absolute(), "logs")
            log_file_path = os.path.join(logs_path, f"{username}.log")
            try:
                with open(log_file_path, "r", encoding="utf-8") as log_file:
                    log_content = log_file.read()

                # Extract new log entries since the last received index
                new_log_entries = log_content[last_received_index:]
                last_sent_log_index = len(log_content)  # Update the last sent index

                return Response(new_log_entries, status=200, mimetype="text/plain")

            except FileNotFoundError:
                return Response("Log file not found.", status=404, mimetype="text/plain")

        self.app = Flask(
            __name__,
            template_folder=os.path.join(Path().absolute(), "assets"),
            static_folder=os.path.join(Path().absolute(), "assets"),
        )
        self.app.add_url_rule(
            "/",
            "index",
            index,
            defaults={"refresh": refresh, "days_ago": days_ago},
            methods=["GET"],
        )
        self.app.add_url_rule("/streamers", "streamers",
                              streamers, methods=["GET"])
        self.app.add_url_rule(
            "/json/<string:streamer>", "json", read_json, methods=["GET"]
        )
        self.app.add_url_rule("/json_all", "json_all",
                              json_all, methods=["GET"])
        self.app.add_url_rule(
            "/log", "log", generate_log, methods=["GET"])
        self.app.add_url_rule(
            "/dry_run/<string:streamer>",
            "dry_run",
            dry_run,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/dry_run_summary/<string:streamer>",
            "dry_run_summary",
            dry_run_summary,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/config",
            "config_editor",
            config_editor_page,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/api/config",
            "config_read",
            config_read,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/api/config/validate",
            "config_validate",
            config_validate,
            methods=["POST"],
        )
        self.app.add_url_rule(
            "/api/config/save",
            "config_save",
            config_save,
            methods=["POST"],
        )
        self.app.add_url_rule(
            "/health",
            "health",
            health,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/api/config/export",
            "config_export",
            config_export_runpy,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/api/export/csv",
            "export_csv",
            export_csv,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/api/export/json",
            "export_json",
            export_json,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/api/global_stats",
            "global_stats",
            global_stats,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/api/discord/backimport",
            "discord_backimport",
            discord_backimport,
            methods=["POST"],
        )
        self.app.add_url_rule(
            "/api/logs/backimport",
            "log_backimport",
            log_backimport,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/api/telemetry/import",
            "telemetry_import",
            telemetry_import,
            methods=["POST"],
        )
        self.app.add_url_rule(
            "/api/telemetry/stats",
            "telemetry_stats",
            telemetry_stats,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/api/telemetry/predictions",
            "telemetry_predictions",
            telemetry_predictions,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/api/telemetry/summary",
            "telemetry_summary",
            telemetry_summary,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/api/config/reload",
            "config_reload",
            config_reload,
            methods=["POST"],
        )
        self.app.add_url_rule(
            "/api/strategy/switch",
            "strategy_switch",
            strategy_switch,
            methods=["POST"],
        )
        self.app.add_url_rule(
            "/api/strategy/switch_all",
            "strategy_switch_all",
            strategy_switch_all,
            methods=["POST"],
        )
        self.app.add_url_rule(
            "/api/strategy/auto_adjust",
            "auto_adjust_config",
            auto_adjust_config,
            methods=["GET", "POST"],
        )
        self.app.add_url_rule(
            "/api/discord/summary",
            "discord_summary",
            discord_summary,
            methods=["POST"],
        )
        self.app.add_url_rule(
            "/api/discord/cleanup",
            "discord_cleanup",
            discord_cleanup,
            methods=["POST"],
        )
        self.app.add_url_rule(
            "/api/discord/channel_log",
            "discord_channel_log",
            discord_channel_log,
            methods=["POST"],
        )
        self.app.add_url_rule(
            "/api/telemetry/export/db",
            "telemetry_export_db",
            telemetry_export_db,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/api/telemetry/export/json",
            "telemetry_export_json",
            telemetry_export_json,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/api/telemetry/import_db",
            "telemetry_import_db",
            telemetry_import_db,
            methods=["POST"],
        )

    def run(self):
        global _server_start_time, _telemetry
        import time

        _server_start_time = time.time()

        # Initialise SQLite telemetry
        try:
            _telemetry = Telemetry()
            logger.info("Telemetry database initialised at %s", _telemetry.db_path)
        except Exception:
            logger.warning("Failed to initialise telemetry DB", exc_info=True)

        logger.info(
            f"Analytics running on http://{self.host}:{self.port}/",
            extra={"emoji": ":globe_with_meridians:"},
        )
        self.app.run(host=self.host, port=self.port,
                     threaded=True, debug=False)
