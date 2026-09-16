# -*- coding: utf-8 -*-
"""
דף הטריגרים — שרת מקומי.

מרכז שליטה לאוטומציות. מריץ אותן ברקע ומזרים את הפלט לדפדפן בזמן אמת.

הרצה:
    python server.py

ואז לפתוח בדפדפן:  http://127.0.0.1:8765  (או הפורט שבתצורה)

האוטומציות, הכותרות, הלוגו והפורט — כולם ב-automations.json.
הקוד הזה לא מכיר אף אוטומציה ואף נתיב אישי.

עקרונות:
  * ספרייה סטנדרטית בלבד. אפס pip install, אפס npm.
  * מאזין על 127.0.0.1 בלבד — לא נגיש מהרשת.
  * הפקודה נבנית כרשימת ארגומנטים ורצה בלי shell.
    נתיבים עם רווחים ועברית עוברים בלי בעיות ציטוט, ואין הזרקת פקודות.
  * לא נוגע ב-run.py ולא קורא את .env. הסקריפט טוען אותו לבד.

להוסיף אוטומציה = להוסיף מילון אחד ל-AUTOMATIONS למטה.
הדפדפן בונה את הכרטיס לבד מההגדרה. אין מה לגעת ב-HTML.
"""

import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# --- קונסולת Windows: UTF-8, אחרת הדפסות בעברית מפילות את השרת ---------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

CONFIG_PATH = HERE / "automations.json"
EXAMPLE_PATH = HERE / "automations.example.json"

AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".mp4", ".aac", ".ogg",
                    ".flac", ".webm", ".mkv", ".mov", ".wma", ".opus"}
MAX_AUDIO_RESULTS = 40

DEFAULT_SETTINGS = {
    "title": "מרכז השליטה",
    "subtitle": "הרצת אוטומציות בלי טרמינל",
    "logo": "",
    "host": "127.0.0.1",
    "port": 8765,
    "file_search_dirs": ["{HOME}/Downloads", "{HOME}/Desktop", "{HOME}/Documents"],
}


# =========================================================================
# התצורה — automations.json
# =========================================================================
# הקוד הזה לא מכיר אף אוטומציה. הוא קורא אותן מקובץ.
#
# להוסיף אוטומציה = להוסיף אובייקט אחד למערך "automations" בקובץ התצורה.
# אין מה לגעת לא כאן ולא ב-HTML — הדפדפן בונה את הכרטיס מההגדרה.
#
#   command   רשימת ארגומנטים. "{שם_שדה}" מוחלף בערך מהטופס.
#             ארגומנט שכל כולו placeholder של שדה ריק — נשמט,
#             וגם הארגומנט שלפניו אם הוא דגל (מתחיל ב--).
#   fields    type: audiofile | number | date | text
#             date נפתח על היום; "default_today": false פותח אותו ריק.
#             "format": "dotted" ממיר YYYY-MM-DD ל-DD.MM.YYYY לפני ההרצה.
#   cost_confirm  true => הדפדפן מבקש אישור לפני הרצה. לפעולות יקרות.
#
# בתוך "cwd", בתוך "command" ובתוך "logo" אפשר להשתמש בקיצורי נתיב:
#   {HOME}       תיקיית הבית של המשתמש
#   {REPO_ROOT}  שורש הריפו (שתי רמות מעל הקובץ הזה)
#   {HERE}       התיקייה של server.py
#   {PYTHON}     ה-Python שמריץ את השרת עצמו
# =========================================================================

PATH_TOKENS = {
    "{HOME}": str(Path.home()),
    "{REPO_ROOT}": str(REPO_ROOT),
    "{HERE}": str(HERE),
    "{PYTHON}": sys.executable,
}


def expand(text):
    """מרחיב קיצורי נתיב. placeholders של שדות ({file}) לא נוגעים."""
    result = str(text)
    for token, value in PATH_TOKENS.items():
        result = result.replace(token, value)
    return result


def load_config():
    """
    קורא את התצורה. מחזיר (settings, automations, path, error).

    automations.json הוא הקובץ של המשתמש. אם הוא לא קיים — נטען
    automations.example.json, כדי שהדף יעבוד מהרגע הראשון גם למי
    שרק הוריד אותו ועוד לא הגדיר כלום.
    """
    path = CONFIG_PATH if CONFIG_PATH.is_file() else EXAMPLE_PATH
    settings = dict(DEFAULT_SETTINGS)

    if not path.is_file():
        return settings, [], None, (
            "No config file. Expected {} or {}".format(
                CONFIG_PATH.name, EXAMPLE_PATH.name))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return settings, [], path, "Cannot read {}: {}".format(path.name, exc)

    settings.update(raw.get("settings") or {})
    automations = raw.get("automations") or []

    for item in automations:
        item["cwd"] = expand(item.get("cwd") or str(REPO_ROOT))
        item["command"] = [expand(token) for token in item.get("command", [])]
        item.setdefault("fields", [])

    ids = [a.get("id") for a in automations]
    if len(set(ids)) != len(ids):
        return settings, [], path, "Duplicate automation id in {}".format(path.name)
    if any(not a.get("id") or not a.get("command") for a in automations):
        return settings, [], path, "Every automation needs id and command"

    return settings, automations, path, None


SETTINGS, AUTOMATIONS, CONFIG_USED, CONFIG_ERROR = load_config()

HOST = str(SETTINGS["host"])
PORT = int(SETTINGS["port"])

AUDIO_SEARCH_DIRS = [Path(expand(d)) for d in SETTINGS["file_search_dirs"]]

LOGO_PATH = None
if SETTINGS.get("logo"):
    _logo = Path(expand(SETTINGS["logo"]))
    LOGO_PATH = _logo if _logo.is_absolute() else (HERE / _logo).resolve()

AUTOMATIONS_BY_ID = {a["id"]: a for a in AUTOMATIONS}


# =========================================================================
# ניהול ריצות
# =========================================================================

class Run:
    """ריצה אחת של אוטומציה. חיה בזיכרון, מתה עם השרת."""

    def __init__(self, run_id, automation_id, argv, cwd):
        self.id = run_id
        self.automation_id = automation_id
        self.argv = argv
        self.cwd = cwd
        self.lines = []
        self.status = "running"        # running | done | error | cancelled
        self.exit_code = None
        self.started_at = time.time()
        self.finished_at = None
        self.process = None
        self.lock = threading.Lock()

    def append(self, text):
        with self.lock:
            self.lines.append(text)

    def snapshot(self, offset):
        with self.lock:
            new_lines = self.lines[offset:]
            total = len(self.lines)
            return {
                "run_id": self.id,
                "automation_id": self.automation_id,
                "status": self.status,
                "exit_code": self.exit_code,
                "lines": new_lines,
                "next_offset": total,
                "elapsed": int((self.finished_at or time.time()) - self.started_at),
            }


RUNS = {}
RUNS_LOCK = threading.Lock()
ACTIVE_BY_AUTOMATION = {}   # automation_id -> run_id, מונע שתי ריצות במקביל


def _pump(run):
    """קורא את הפלט של התהליך שורה-שורה ומזרים אותו ל-run.lines."""
    try:
        for line in run.process.stdout:
            run.append(line.rstrip("\n"))
    except Exception as exc:                       # noqa: BLE001
        run.append("[שגיאת קריאת פלט] {}".format(exc))
    finally:
        code = run.process.wait()
        run.exit_code = code
        run.finished_at = time.time()
        with run.lock:
            if run.status == "cancelled":
                pass
            elif code == 0:
                run.status = "done"
            else:
                run.status = "error"
        with RUNS_LOCK:
            if ACTIVE_BY_AUTOMATION.get(run.automation_id) == run.id:
                del ACTIVE_BY_AUTOMATION[run.automation_id]


def build_argv(automation, values):
    """
    בונה את רשימת הארגומנטים. מחזיר (argv, error).
    שדה ריק שאינו חובה מפיל את עצמו ואת הדגל שלפניו.
    """
    fields = {f["name"]: f for f in automation["fields"]}
    argv = []
    for token in automation["command"]:
        if token.startswith("{") and token.endswith("}"):
            field_name = token[1:-1]
            value = str(values.get(field_name, "")).strip()
            if not value:
                if argv and argv[-1].startswith("--"):
                    argv.pop()
                continue
            # הדפדפן שולח תאריך תמיד כ-YYYY-MM-DD. סקריפט שמצפה לפורמט אחר
            # מצהיר על כך בשדה: "format": "dotted".
            if fields.get(field_name, {}).get("format") == "dotted":
                try:
                    value = (datetime.strptime(value, "%Y-%m-%d")
                             .strftime("%d.%m.%Y"))
                except ValueError:
                    return None, "תאריך לא תקין בשדה {}: {}".format(
                        fields[field_name]["label"], value)
            argv.append(value)
        else:
            argv.append(token)
    return argv, None


def validate(automation, values):
    """בדיקות לפני שמוציאים כסף. מחזיר רשימת שגיאות בעברית."""
    errors = []
    for field in automation["fields"]:
        name = field["name"]
        value = str(values.get(name, "")).strip()

        if field.get("required") and not value:
            errors.append("חסר שדה חובה: {}".format(field["label"]))
            continue
        if not value:
            continue

        if field["type"] == "number":
            if not value.lstrip("-").isdigit():
                errors.append("{} חייב להיות מספר שלם. התקבל: {}".format(
                    field["label"], value))
            elif "min" in field and int(value) < field["min"]:
                errors.append("{} חייב להיות לפחות {}.".format(
                    field["label"], field["min"]))

        elif field["type"] == "date":
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                errors.append(
                    "{} חייב להיות בפורמט YYYY-MM-DD. התקבל: {}".format(
                        field["label"], value))

        elif field["type"] == "audiofile":
            path = Path(value)
            if not path.exists():
                errors.append("הקובץ לא נמצא: {}".format(value))
            elif not path.is_file():
                errors.append("הנתיב אינו קובץ: {}".format(value))

    cwd = Path(automation["cwd"])
    if not cwd.is_dir():
        errors.append("תיקיית ההרצה לא קיימת: {}".format(cwd))

    # פקודה שמתחילה בנתיב מלא (למשל Python של .venv) — לוודא שהוא קיים.
    interpreter = Path(automation["command"][0])
    if interpreter.is_absolute() and not interpreter.is_file():
        errors.append(
            "לא נמצא: {}\n"
            "צריך ליצור את הסביבה הווירטואלית. ראה README של ה-pipeline, "
            "סעיף \"סביבת Python\".".format(interpreter))
    return errors


def start_run(automation, values):
    """מפעיל את האוטומציה ברקע. מחזיר (run, error_message)."""
    with RUNS_LOCK:
        if automation["id"] in ACTIVE_BY_AUTOMATION:
            return None, "האוטומציה הזאת כבר רצה. חכה שתסתיים או עצור אותה."

    argv, err = build_argv(automation, values)
    if err:
        return None, err

    run_id = uuid.uuid4().hex[:12]
    run = Run(run_id, automation["id"], argv, automation["cwd"])

    env = os.environ.copy()
    # בלי אלה הפלט של python מגיע בגושים ורק בסוף — הדף היה נראה תקוע.
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # שתי השורות האלה מתחילות ב-ASCII בכוונה: unicode-bidi:plaintext בדף
    # נותן לכל שורה כיוון לפי התו החזק הראשון, ונתיב בשורה שמתחילה
    # בעברית היה נשבר לקריאה.
    run.append("$ " + " ".join(argv))
    run.append("  cwd: " + automation["cwd"])
    run.append("")

    try:
        run.process = subprocess.Popen(
            argv,
            cwd=automation["cwd"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
            shell=False,
        )
    except FileNotFoundError:
        return None, ("לא נמצאה הפקודה '{}'. ודא ש-Python מותקן ונמצא ב-PATH."
                      .format(argv[0]))
    except Exception as exc:                       # noqa: BLE001
        return None, "כשל בהפעלת התהליך: {}".format(exc)

    with RUNS_LOCK:
        RUNS[run_id] = run
        ACTIVE_BY_AUTOMATION[automation["id"]] = run_id

    threading.Thread(target=_pump, args=(run,), daemon=True).start()
    return run, None


def cancel_run(run_id):
    run = RUNS.get(run_id)
    if not run:
        return False, "ריצה לא נמצאה."
    if run.status != "running":
        return False, "הריצה כבר הסתיימה."
    with run.lock:
        run.status = "cancelled"
        run.lines.append("")
        run.lines.append("⛔ הריצה נעצרה על ידי המשתמש.")
    try:
        run.process.terminate()
    except Exception as exc:                       # noqa: BLE001
        return False, "כשל בעצירה: {}".format(exc)
    return True, None


def list_audio_files():
    """סורק תיקיות מוכרות ומחזיר קבצי מדיה, החדשים קודם."""
    found = []
    for directory in AUDIO_SEARCH_DIRS:
        if not directory.is_dir():
            continue
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                if entry.suffix.lower() not in AUDIO_EXTENSIONS:
                    continue
                stat = entry.stat()
            except OSError:
                continue
            found.append({
                "name": entry.name,
                "path": str(entry),
                "folder": directory.name,
                "size_mb": round(stat.st_size / (1024 * 1024), 1),
                "mtime": stat.st_mtime,
                "modified": datetime.fromtimestamp(stat.st_mtime)
                                    .strftime("%Y-%m-%d"),
            })
    found.sort(key=lambda item: item["mtime"], reverse=True)
    return found[:MAX_AUDIO_RESULTS]


# =========================================================================
# HTTP
# =========================================================================

class Handler(BaseHTTPRequestHandler):
    server_version = "TriggerPage/1.0"

    def log_message(self, fmt, *args):
        pass  # השרת שקט. הפלט המעניין הוא של הסקריפטים.

    # ---- עזרי תגובה ----

    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body)

    def _file(self, path):
        if not path.is_file():
            self._json({"error": "לא נמצא: {}".format(path)}, 404)
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype)

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8") or "{}"), None
        except Exception as exc:                   # noqa: BLE001
            return None, "גוף הבקשה אינו JSON תקין: {}".format(exc)

    # ---- ניתוב ----

    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if route in ("/", "/index.html"):
            self._file(HERE / "index.html")

        elif route == "/assets/logo.png":
            if LOGO_PATH is None:
                # אין לוגו מוגדר. הדפדפן מסיר את התמונה לבד (onerror).
                self._json({"error": "no logo configured"}, 404)
                return
            self._file(LOGO_PATH)

        elif route == "/api/automations":
            self._json({
                "automations": AUTOMATIONS,
                "settings": {
                    "title": SETTINGS["title"],
                    "subtitle": SETTINGS["subtitle"],
                    "address": "{}:{}".format(HOST, PORT),
                    "has_logo": bool(LOGO_PATH and LOGO_PATH.is_file()),
                },
                "repo_root": str(REPO_ROOT),
                "today": datetime.now().strftime("%Y-%m-%d"),
            })

        elif route == "/api/audio-files":
            self._json({"files": list_audio_files()})

        elif route == "/api/status":
            run_id = (query.get("run_id") or [""])[0]
            offset = int((query.get("offset") or ["0"])[0])
            run = RUNS.get(run_id)
            if not run:
                self._json({"error": "ריצה לא נמצאה."}, 404)
                return
            self._json(run.snapshot(offset))

        else:
            self._json({"error": "לא נמצא: {}".format(route)}, 404)

    def do_POST(self):
        route = urlparse(self.path).path
        payload, err = self._read_json_body()
        if err:
            self._json({"error": err}, 400)
            return

        if route == "/api/run":
            automation = AUTOMATIONS_BY_ID.get(payload.get("automation_id"))
            if not automation:
                self._json({"error": "אוטומציה לא מוכרת."}, 400)
                return

            values = payload.get("values") or {}
            errors = validate(automation, values)
            if errors:
                self._json({"error": "\n".join(errors)}, 400)
                return

            run, error = start_run(automation, values)
            if error:
                self._json({"error": error}, 400)
                return
            self._json({"run_id": run.id, "command": " ".join(run.argv)})

        elif route == "/api/cancel":
            ok, error = cancel_run(payload.get("run_id"))
            if not ok:
                self._json({"error": error}, 400)
                return
            self._json({"ok": True})

        else:
            self._json({"error": "לא נמצא: {}".format(route)}, 404)


class ExclusiveHTTPServer(ThreadingHTTPServer):
    """
    http.server מדליק SO_REUSEADDR כברירת מחדל. ב-Windows זה מרשה לשני
    תהליכים להאזין על אותו פורט בלי שגיאה, והבקשות מתפצלות ביניהם:
    הרצה שהתחילה בשרת אחד "לא נמצאת" בשני, בזמן שהיא ממשיכה לרוץ ולחייב.
    כיבוי ההגדרה מחזיר את השגיאה "הפורט תפוס" לשרת השני.
    נמצא ב-2026-09-13: start.bat עלה ליד שרת פתוח בטרמינל.
    """
    allow_reuse_address = False


def main():
    # הודעות הקונסולה באנגלית ו-ASCII בכוונה: הקונסולה של Windows לא
    # מציגה RTL, ועברית יוצאת בה הפוכה ("המלש לש..."). כמו ב-start.bat.
    url = "http://{}:{}".format(HOST, PORT)

    # תצורה שבורה עוצרת כאן, לפני שתופסים פורט. הודעה אחת ברורה
    # עדיפה על דף שעולה ריק בלי שהמשתמש יודע למה.
    if CONFIG_ERROR:
        print("")
        print("  [X] Config error: {}".format(CONFIG_ERROR))
        print("      File: {}".format(CONFIG_USED or CONFIG_PATH))
        sys.exit(1)

    # קודם תופסים את הפורט, ורק אז מדפיסים "השאר את החלון פתוח".
    # אחרת חלון שנכשל היה מבקש להשאיר אותו פתוח.
    try:
        httpd = ExclusiveHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        print("")
        print("  [X] Cannot listen on {}:{} - {}".format(HOST, PORT, exc))
        print("      The server is probably already running in another")
        print("      window. Use that one, or close it and try again.")
        sys.exit(1)

    print("")
    print("  Trigger Page")
    print("  " + "-" * 46)
    print("  URL:          {}".format(url))
    print("  Config:       {}".format(
        CONFIG_USED.name if CONFIG_USED else "(none)"))
    print("  Automations:  {}".format(len(AUTOMATIONS)))
    if CONFIG_USED == EXAMPLE_PATH:
        print("  " + "-" * 46)
        print("  Running on the EXAMPLE config.")
        print("  Copy automations.example.json to automations.json")
        print("  and edit it to add your own automations.")
    print("  " + "-" * 46)
    print("  Keep this window open. Closing it stops the")
    print("  server AND any automation that is running.")
    print("  Stop: Ctrl+C")
    print("")

    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
        for run in list(RUNS.values()):
            if run.status == "running" and run.process:
                try:
                    run.process.terminate()
                except Exception:                  # noqa: BLE001
                    pass
        httpd.server_close()


if __name__ == "__main__":
    main()
