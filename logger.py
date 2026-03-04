from bottle import get, post, run, request, response, route, ServerAdapter
import json
import os
from pymongo import MongoClient
from datetime import datetime
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server


class _QuietHandler(WSGIRequestHandler):
    """Suppress ConnectionResetError caused by clients dropping the connection."""
    def handle(self):
        try:
            super().handle()
        except ConnectionResetError:
            pass

    def log_message(self, format, *args):  # noqa: A002
        pass  # silence per-request access log (goes to journald anyway)


class _QuietServer(WSGIServer):
    def handle_error(self, request, client_address):
        pass  # suppress tracebacks for all socket-level errors


class QuietWSGIRefServer(ServerAdapter):
    """wsgiref adapter that swallows ConnectionResetError from impatient clients."""
    def run(self, app):
        httpd = make_server(
            self.host, self.port, app,
            server_class=_QuietServer,
            handler_class=_QuietHandler,
        )
        httpd.serve_forever()

# Konfiguracja połączenia
client = MongoClient('mongodb://admin:Lato2020!@localhost:27017/')
db = client['system_logs']
collection = db['user_logs']

DEFAULT_PAGE_SIZE = 200
ALLOWED_LOCAL_IPS = {"127.0.0.1", "::1"}
ALLOWED_CORS_ORIGINS = {"*"}

def _apply_cors_headers():
    origin = request.headers.get("Origin")
    allow_origin = "*" if "*" in ALLOWED_CORS_ORIGINS else (origin if origin in ALLOWED_CORS_ORIGINS else None)
    if allow_origin:
        response.set_header("Access-Control-Allow-Origin", allow_origin)
        response.set_header("Vary", "Origin")
    response.set_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    response.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
    response.set_header("Access-Control-Max-Age", "600")

def _require_local():
    remote_addr = request.remote_addr or request.environ.get("REMOTE_ADDR")
    if remote_addr not in ALLOWED_LOCAL_IPS:
        response.status = 403
        return {"status": "error", "message": "Forbidden"}
    return None

def _parse_date(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

def _serialize_log(doc):
    if not doc:
        return doc
    doc = dict(doc)
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    if "timestamp" in doc and isinstance(doc["timestamp"], datetime):
        doc["timestamp"] = doc["timestamp"].isoformat() + "Z"
    return doc

@post(['/log', '/log/'])
def save_log():
    access_error = _require_local()
    if access_error:
        return access_error
    # Pobieranie danych JSON z requestu
    log_data = request.json
    
    if not log_data:
        response.status = 400
        return {"status": "error", "message": "Brak danych JSON"}

    # Dodanie znacznika czasu po stronie serwera logów
    log_data['timestamp'] = datetime.utcnow()
    log_data['logtype'] = 'backend' if log_data.get('logtype') and log_data.get('logtype') == 'backend' else 'frontend'
    
    # Zapis do MongoDB
    log_id = collection.insert_one(log_data).inserted_id
    
    return {"status": "success", "id": str(log_id)}


# Example GET: /log?method=GET&controller=Auth&user=42&created_from=2026-01-01&created_to=2026-02-01&page=1&order_by=timestamp&order_dir=desc
@get(['/log', '/log/'])
def get_logs():
    access_error = _require_local()
    if access_error:
        return access_error
    filters = {}

    allowed_order_fields = {
        "client_ip",
        "timestamp",
        "user_email",
        "controller",
        "user",
        "method",
        "status_code",
        "duration",
    }

    method = request.query.get("method")
    if method:
        filters["method"] = method

    response = request.query.get("response")
    if response:
        filters["response"] = response

    logType = request.query.get("logType")
    if logType:
        filters["logtype"] = logType

    logtype = request.query.get("logtype")
    if logtype:
        filters["logtype"] = logtype

    controller = request.query.get("controller")
    if controller:
        filters["controller"] = {"$regex": controller, "$options": "i"}

    user_email = request.query.get("user_email")
    if user_email:
        filters["user_email"] = {"$regex": user_email, "$options": "i"}

    search_text = request.query.get("search_text")
    if search_text:
        filters["$or"] = [
            {"data": {"$regex": search_text, "$options": "i"}},
            {"response_body": {"$regex": search_text, "$options": "i"}},
        ]

    fk_id = request.query.get("fk_id")
    if fk_id:
        filters["path"] = {"$regex": fk_id, "$options": "i"}

    user_value = request.query.get("user")
    if user_value is not None:
        try:
            filters["user"] = int(user_value)
        except ValueError:
            response.status = 400
            return {"status": "error", "message": "Invalid user value"}

    created_from = _parse_date(request.query.get("created_from"))
    created_to = _parse_date(request.query.get("created_to"))
    if request.query.get("created_from") and not created_from:
        response.status = 400
        return {"status": "error", "message": "Invalid created_from value"}
    if request.query.get("created_to") and not created_to:
        response.status = 400
        return {"status": "error", "message": "Invalid created_to value"}

    if created_from or created_to:
        filters["timestamp"] = {}
        if created_from:
            filters["timestamp"]["$gte"] = created_from
        if created_to:
            filters["timestamp"]["$lte"] = created_to

    page_raw = request.query.get("page", "1")
    try:
        page = int(page_raw)
    except ValueError:
        response.status = 400
        return {"status": "error", "message": "Invalid page value"}
    if page < 1:
        response.status = 400
        return {"status": "error", "message": "Page must be >= 1"}

    page_size = DEFAULT_PAGE_SIZE
    skip = (page - 1) * page_size

    total = collection.count_documents(filters)
    order_by = request.query.get("order_by", "timestamp")
    if order_by not in allowed_order_fields:
        response.status = 400
        return {"status": "error", "message": "Invalid order_by value"}

    order_dir_raw = request.query.get("order_dir", "desc").lower()
    if order_dir_raw not in {"asc", "desc"}:
        response.status = 400
        return {"status": "error", "message": "Invalid order_dir value"}
    order_dir = 1 if order_dir_raw == "asc" else -1

    cursor = collection.find(filters).sort(order_by, order_dir).skip(skip).limit(page_size)
    items = [_serialize_log(doc) for doc in cursor]

    return {
        "status": "success",
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
        "items": items,
    }

@get(['/frontend-config', '/frontend-config/'])
def get_frontend_config():
    _apply_cors_headers()
    config_path = os.path.join(os.path.dirname(__file__), "frontend.conf.json")
    whitelist_path = os.path.join(os.path.dirname(__file__), "whitelist.json")
    if not os.path.isfile(config_path):
        response.status = 404
        return {"status": "error", "message": "Config not found"}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
    except (OSError, json.JSONDecodeError):
        response.status = 500
        return {"status": "error", "message": "Failed to load config"}

    whitelist_ips = []
    if os.path.isfile(whitelist_path):
        try:
            with open(whitelist_path, "r", encoding="utf-8") as f:
                whitelist_data = json.load(f)
            whitelist_ips = whitelist_data.get("ip", []) if isinstance(whitelist_data, dict) else []
        except (OSError, json.JSONDecodeError):
            whitelist_ips = []

    remote_addr = request.remote_addr or request.environ.get("REMOTE_ADDR")
    if remote_addr in whitelist_ips:
        config_data["maintenece_mode"] = False

    response.content_type = "application/json"
    return config_data

@route(['/frontend-config', '/frontend-config/'], method='OPTIONS')
def frontend_config_options():
    _apply_cors_headers()
    response.status = 204
    return ""

if __name__ == "__main__":
    # Uruchomienie na porcie 8081, aby nie kolidowało z FastAPI (domyślnie 8000)
    run(host='0.0.0.0', port=8082, debug=False, server=QuietWSGIRefServer)