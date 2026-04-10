import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from threading import Thread

import pandas as pd
from flask import Flask, Response, cli, render_template, request

from TwitchChannelPointsMiner.classes.Settings import Settings
from TwitchChannelPointsMiner.utils import download_file

cli.show_server_banner = lambda *_: None
logger = logging.getLogger(__name__)


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

    original_series = datas["series"]

    if "series" in datas:
        df = pd.DataFrame(datas["series"])
        df["datetime"] = pd.to_datetime(df.x // 1000, unit="s")

        df = df[(df.x >= start_date) & (df.x <= end_date)]

        datas["series"] = (
            df.drop(columns="datetime")
            .sort_values(by=["x", "y"], ascending=True)
            .to_dict("records")
        )
    else:
        datas["series"] = []

    # If no data is found within the timeframe, that usually means the streamer hasn't streamed within that timeframe
    # We create a series that shows up as a straight line on the dashboard, with 'No Stream' as labels
    if len(datas["series"]) == 0:
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
        json.dumps(result), status=200, mimetype="application/json"
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
    """Read the current run.py config file."""
    config_path = os.path.join(Path().absolute(), "run.py")
    if not os.path.isfile(config_path):
        # Try example.py as fallback
        config_path = os.path.join(Path().absolute(), "example.py")
    if not os.path.isfile(config_path):
        return Response(
            json.dumps({"error": "No config file found (run.py or example.py)."}),
            status=404,
            mimetype="application/json",
        )
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
        return Response(
            json.dumps({"content": content, "path": config_path}),
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
    """Validate Python syntax of submitted config content."""
    data = request.get_json(silent=True)
    if not data or "content" not in data:
        return Response(
            json.dumps({"valid": False, "error": "No content provided."}),
            status=400,
            mimetype="application/json",
        )
    content = data["content"]
    try:
        compile(content, "<config>", "exec")
        return Response(
            json.dumps({"valid": True}),
            status=200,
            mimetype="application/json",
        )
    except SyntaxError as e:
        return Response(
            json.dumps({
                "valid": False,
                "error": f"Line {e.lineno}: {e.msg}",
                "lineno": e.lineno,
            }),
            status=200,
            mimetype="application/json",
        )


def config_save():
    """Save config after validation, creating a backup first."""
    data = request.get_json(silent=True)
    if not data or "content" not in data:
        return Response(
            json.dumps({"success": False, "error": "No content provided."}),
            status=400,
            mimetype="application/json",
        )
    content = data["content"]

    # Syntax check first
    try:
        compile(content, "<config>", "exec")
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
    if not os.path.isfile(config_path):
        config_path = os.path.join(Path().absolute(), "example.py")

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
        logger.info("Config file saved via web editor")
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

    def run(self):
        logger.info(
            f"Analytics running on http://{self.host}:{self.port}/",
            extra={"emoji": ":globe_with_meridians:"},
        )
        self.app.run(host=self.host, port=self.port,
                     threaded=True, debug=False)
