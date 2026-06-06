from bottle import Bottle, get, post, run, request, response, route, ServerAdapter
import json
import os
import re
from pymongo import MongoClient
from datetime import datetime, timezone
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server

# Create Bottle application instance for WSGI servers (gunicorn, uWSGI, etc.)
app = Bottle()


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

DEFAULT_PAGE_SIZE = 20
ALLOWED_LOCAL_IPS = {"127.0.0.1", "::1"}
ALLOWED_CORS_ORIGINS = {"*"}

# Pre-compute file paths once at module load
_BASE_DIR = os.path.dirname(__file__)
_CONFIG_PATH = os.path.join(_BASE_DIR, "frontend.conf.json")
_WHITELIST_PATH = os.path.join(_BASE_DIR, "whitelist.json")

# Cache configuration for /frontend-config endpoint
_config_cache = {
    "data": None,
    "whitelist": [],
    "last_modified": 0,
    "whitelist_modified": 0,
    "last_loaded": 0,
    "last_mtime_check": 0,  # Only check file mtimes every N seconds
    "ttl": 60,  # Cache for 60 seconds
    "mtime_check_interval": 5,  # Check file modification times every 5 seconds
    # Pre-serialized JSON responses (fastest possible serving)
    "json_normal": None,  # JSON string for normal users
    "json_whitelisted": None,  # JSON string for whitelisted IPs (maintenance_mode=false)
}
_config_cache_lock = False  # Simple lock for cache updates

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


def _fix_mojibake(text):
    """Detect and fix double-encoded UTF-8 (mojibake).
    If UTF-8 bytes were incorrectly interpreted as Latin-1, fix them."""
    try:
        # Try to encode as Latin-1, then decode as UTF-8
        # This reverses the mojibake: Åº → ź
        return text.encode('latin1').decode('utf-8')
    except (UnicodeDecodeError, UnicodeEncodeError):
        # Not mojibake, return original
        return text


def _build_search_regex(text):
    """Build a MongoDB regex pattern converting non-ASCII characters to their
    \\uXXXX literal form, matching how json.dumps(ensure_ascii=True) stores them."""
    pattern = ''
    for char in text:
        if ord(char) > 127:
            # e.g. ź (U+017A) → \\u017a in the regex → matches literal \u017a in stored data
            pattern += '\\\\u{:04x}'.format(ord(char))
        else:
            pattern += re.escape(char)
    return pattern

def _serialize_log(doc):
    if not doc:
        return doc
    doc = dict(doc)
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    if "timestamp" in doc and isinstance(doc["timestamp"], datetime):
        doc["timestamp"] = doc["timestamp"].isoformat() + "Z"
    return doc

@app.post(['/log', '/log/'])
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
    log_data['timestamp'] = datetime.now(timezone.utc)
    log_data['logtype'] = 'backend' if log_data.get('logtype') and log_data.get('logtype') == 'backend' else 'frontend'

    # Normalize `data` to a UTF-8 JSON string (no \uXXXX escapes)
    if 'data' in log_data and log_data['data'] is not None:
        d = log_data['data']
        if isinstance(d, str):
            try:
                # Re-encode parsed JSON with ensure_ascii=True to store \uXXXX escapes (pure ASCII)
                log_data['data'] = json.dumps(json.loads(d), ensure_ascii=True)
            except (ValueError, TypeError):
                log_data['data'] = d
        elif isinstance(d, (dict, list)):
            log_data['data'] = json.dumps(d, ensure_ascii=True)

    # Ensure `response_body` is a UTF-8 string of at most 512 characters
    if 'response_body' in log_data and log_data['response_body'] is not None:
        rb = log_data['response_body']
        if isinstance(rb, str):
            try:
                rb = json.dumps(json.loads(rb), ensure_ascii=True)
            except (ValueError, TypeError):
                pass
        else:
            try:
                rb = json.dumps(rb, ensure_ascii=True)
            except Exception:
                rb = str(rb)
        if len(rb) > 512:
            rb = rb[:512]
        log_data['response_body'] = rb

    # Zapis do MongoDB
    log_id = collection.insert_one(log_data).inserted_id
    
    return {"status": "success", "id": str(log_id)}


# Example GET: /log?method=GET&controller=Auth&user=42&created_from=2026-01-01&created_to=2026-02-01&page=1&order_by=timestamp&order_dir=desc
@app.get(['/log', '/log/'])
def get_logs():
    access_error = _require_local()
    if access_error:
        return access_error
    filters = {}
    use_collation = False

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

    # FIX: renamed local variable `response` → `response_val` to avoid shadowing
    # the Bottle `response` object imported at module level.
    response_val = request.query.get("response")
    if response_val:
        filters["response"] = response_val

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
        # Fix mojibake (double-encoded UTF-8)
        search_text = _fix_mojibake(search_text)
        
        # Build a regex where each Polish character matches both accented and
        # plain ASCII variants; $options "i" covers case-insensitivity.
        # NOTE: MongoDB $regex ignores collation, so collation alone cannot
        # handle diacritics — character classes are required.
        # The pattern also matches literal \uXXXX escape sequences that may
        # have been stored by older records with ensure_ascii=True.
        pattern = _build_search_regex(search_text)
        filters["$or"] = [
            {"data": {"$regex": pattern, "$options": "i"}},
            {"response_body": {"$regex": pattern, "$options": "i"}},
        ]
        use_collation = True

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

    page_size = int(request.query.get("limit", DEFAULT_PAGE_SIZE))
    skip = int(request.query.get("skip", (page - 1) * page_size))

    collation = {"locale": "pl", "strength": 1} if use_collation else None

    total = collection.count_documents(filters, collation=collation) if collation else collection.count_documents(filters)
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
    if collation:
        cursor = cursor.collation(collation)
    items = [_serialize_log(doc) for doc in cursor]

    return {
        "status": "success",
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
        "items": items,
    }


def _load_frontend_config_cached():
    """Load frontend config with optimized caching and pre-serialized JSON responses."""
    global _config_cache, _config_cache_lock
    
    current_time = datetime.now(timezone.utc).timestamp()
    
    # Fast path: Return cached data without checking file mtimes if checked recently
    if _config_cache["data"] is not None:
        time_since_mtime_check = current_time - _config_cache["last_mtime_check"]
        if time_since_mtime_check < _config_cache["mtime_check_interval"]:
            # Cache is fresh, skip file I/O completely
            return _config_cache["data"], _config_cache["whitelist"], _config_cache["json_normal"], _config_cache["json_whitelisted"], None
    
    # Slow path: Check if files have been modified (only runs every 5 seconds)
    if not os.path.isfile(_CONFIG_PATH):
        return None, None, None, None, "Config not found"
    
    try:
        config_mtime = os.path.getmtime(_CONFIG_PATH)
        whitelist_mtime = os.path.getmtime(_WHITELIST_PATH) if os.path.isfile(_WHITELIST_PATH) else 0
    except OSError:
        return None, None, None, None, "Failed to check file modification time"
    
    # Update the last mtime check timestamp
    _config_cache["last_mtime_check"] = current_time
    
    # Return cached data if files haven't been modified and cache is valid
    cache_age = current_time - _config_cache["last_loaded"]
    if (
        _config_cache["data"] is not None
        and _config_cache["last_modified"] == config_mtime
        and _config_cache["whitelist_modified"] == whitelist_mtime
        and cache_age < _config_cache["ttl"]
    ):
        return _config_cache["data"], _config_cache["whitelist"], _config_cache["json_normal"], _config_cache["json_whitelisted"], None
    
    # Prevent cache stampede (simple lock)
    if _config_cache_lock:
        # Another thread is loading, return stale cache if available
        if _config_cache["data"] is not None:
            return _config_cache["data"], _config_cache["whitelist"], _config_cache["json_normal"], _config_cache["json_whitelisted"], None
    
    # Reload from disk (files were modified or cache expired)
    _config_cache_lock = True
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            config_data = json.load(f)
        
        whitelist_ips = []
        if os.path.isfile(_WHITELIST_PATH):
            try:
                with open(_WHITELIST_PATH, "r", encoding="utf-8") as f:
                    whitelist_data = json.load(f)
                whitelist_ips = whitelist_data.get("ip", []) if isinstance(whitelist_data, dict) else []
            except (OSError, json.JSONDecodeError):
                whitelist_ips = []
        
        # Pre-serialize JSON responses for maximum speed
        # Normal response (as-is from file)
        json_normal = json.dumps(config_data, ensure_ascii=False)
        
        # Whitelisted response (maintenance_mode forced to false)
        config_whitelisted = dict(config_data)
        config_whitelisted["maintenece_mode"] = False
        json_whitelisted = json.dumps(config_whitelisted, ensure_ascii=False)
        
        # Update cache
        _config_cache["data"] = config_data
        _config_cache["whitelist"] = whitelist_ips
        _config_cache["last_modified"] = config_mtime
        _config_cache["whitelist_modified"] = whitelist_mtime
        _config_cache["last_loaded"] = current_time
        _config_cache["json_normal"] = json_normal
        _config_cache["json_whitelisted"] = json_whitelisted
        
        return config_data, whitelist_ips, json_normal, json_whitelisted, None
        
    except (OSError, json.JSONDecodeError) as e:
        return None, None, None, None, f"Failed to load config: {str(e)}"
    finally:
        _config_cache_lock = False


@app.get(['/frontend-config', '/frontend-config/'])
def get_frontend_config():
    _apply_cors_headers()
    
    # Load config from cache (with pre-serialized JSON)
    config_data, whitelist_ips, json_normal, json_whitelisted, error = _load_frontend_config_cached()
    
    if error:
        response.status = 500 if "Failed to load" in error else 404
        return {"status": "error", "message": error}
    
    # Set headers once
    response.content_type = "application/json; charset=utf-8"
    response.set_header("Cache-Control", "public, max-age=30, s-maxage=60")
    response.set_header("Vary", "Origin")
    
    # Check if requester is whitelisted and return pre-serialized JSON
    remote_addr = request.remote_addr or request.environ.get("REMOTE_ADDR")
    if remote_addr in whitelist_ips:
        return json_whitelisted  # Already serialized, fastest possible response
    
    return json_normal  # Already serialized, fastest possible response

@app.route(['/frontend-config', '/frontend-config/'], method='OPTIONS')
def frontend_config_options():
    _apply_cors_headers()
    response.status = 204
    return ""

if __name__ == "__main__":
    # Development server only - use gunicorn for production
    run(app=app, host='0.0.0.0', port=8082, debug=False, server=QuietWSGIRefServer)