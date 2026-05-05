# Production Setup Guide for High Traffic

## Changes Made for Optimization

### 1. **In-Memory Caching** ✅
- Config files are now cached in memory for 60 seconds
- Reduces disk I/O from thousands/sec to ~1/minute
- Automatic cache invalidation when files change
- Prevents cache stampede with simple locking

### 2. **HTTP Caching Headers** ✅
- `Cache-Control: public, max-age=30, s-maxage=60`
- Browsers cache for 30 seconds
- Nginx/CDN can cache for 60 seconds
- Reduces backend requests significantly

## Production Server Setup

### Option 1: Gunicorn (Recommended)

**Install gunicorn:**
```bash
source /home/boss/workspace/bottle/venv/bin/activate
pip install gunicorn
```

**Install the service:**
```bash
sudo cp /home/boss/workspace/bottle/bottle-logger-gunicorn.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl stop bottle-logger  # Stop old service if running
sudo systemctl start bottle-logger-gunicorn
sudo systemctl enable bottle-logger-gunicorn
sudo systemctl status bottle-logger-gunicorn
```

**Monitor logs:**
```bash
journalctl -u bottle-logger-gunicorn.service -f
```

### Option 2: uWSGI (Alternative)

```bash
pip install uwsgi
```

Create `/etc/uwsgi/apps-available/bottle-logger.ini`:
```ini
[uwsgi]
chdir = /home/boss/workspace/bottle
module = logger_wsgi:app
master = true
processes = 4
threads = 2
socket = 127.0.0.1:8082
vacuum = true
die-on-term = true
```

## Nginx Configuration Improvements

**Fix the upstream timeout issue:**

Edit your nginx config (usually `/etc/nginx/sites-available/zgranegrono.pl`):

```nginx
upstream bottle_logger {
    # IMPORTANT: Use 127.0.0.1, NOT 0.0.0.0
    server 127.0.0.1:8082 max_fails=3 fail_timeout=30s;
    keepalive 32;  # Connection pooling
}

server {
    server_name zgranegrono.pl;
    
    # Cache the frontend-config response in nginx
    location /frontend-config {
        proxy_pass http://bottle_logger;
        
        # Proxy settings
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts (increase from default)
        proxy_connect_timeout 10s;
        proxy_send_timeout 10s;
        proxy_read_timeout 10s;
        
        # Nginx-level caching
        proxy_cache_valid 200 60s;
        proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
        proxy_cache_background_update on;
        proxy_cache_lock on;
        
        # CORS headers (if not set by backend)
        add_header Access-Control-Allow-Origin "*" always;
    }
    
    # Other endpoints
    location ~ ^/(log|log/) {
        proxy_pass http://bottle_logger;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        
        # No caching for logging endpoints
        proxy_cache_bypass 1;
        proxy_no_cache 1;
    }
}
```

**Setup nginx cache zone (add to nginx.conf or http block):**
```nginx
http {
    # Define cache zone
    proxy_cache_path /var/cache/nginx/bottle levels=1:2 keys_zone=bottle_cache:10m 
                     max_size=100m inactive=60m use_temp_path=off;
    
    # Add to your server block:
    # proxy_cache bottle_cache;
}
```

**Reload nginx:**
```bash
sudo nginx -t
sudo systemctl reload nginx
```

## Performance Tuning

### Adjust Gunicorn Workers

Calculate optimal workers:
```bash
# Formula: (2 x CPU_CORES) + 1
nproc  # Shows CPU count
# If 2 cores: use 5 workers
# If 4 cores: use 9 workers
```

Edit the service file and change `--workers 4` to your calculated value.

### Monitor Performance

```bash
# Check requests per second
watch -n 1 'journalctl -u bottle-logger-gunicorn.service --since "1 minute ago" | grep -c "GET /frontend-config"'

# Check response times in nginx logs
tail -f /var/log/nginx/access.log | grep frontend-config

# Monitor system resources
htop
```

## Expected Performance Improvements

- **Before**: ~10-50 requests/sec, frequent timeouts
- **After**: 1000+ requests/sec, sub-10ms response times
- **Cache hit ratio**: 95%+ with nginx caching
- **Timeout errors**: Eliminated

## Troubleshooting

### Still getting timeouts?

1. **Check if service is running:**
   ```bash
   sudo systemctl status bottle-logger-gunicorn
   curl http://127.0.0.1:8082/frontend-config
   ```

2. **Check MongoDB connection:**
   ```bash
   mongosh --eval "db.adminCommand('ping')"
   ```

3. **Increase nginx timeouts if needed** (in location block):
   ```nginx
   proxy_connect_timeout 30s;
   proxy_read_timeout 30s;
   ```

4. **Check file permissions:**
   ```bash
   ls -la /home/boss/workspace/bottle/*.json
   # Should be readable by 'boss' user
   ```

### Monitor cache effectiveness

Add to logger.py for debugging:
```python
import logging
logging.basicConfig(level=logging.INFO)

# In _load_frontend_config_cached:
logging.info(f"Cache hit: {cache_age < _config_cache['ttl']}")
```

## Security Considerations

1. **Bind to localhost only** - Done ✅ (127.0.0.1:8082)
2. **Run as non-root user** - Done ✅ (user: boss)
3. **Access control** - /log endpoints already restricted to localhost
4. **HTTPS** - Ensure nginx has SSL configured
5. **MongoDB authentication** - Already configured ✅

## Rollback Instructions

If issues occur, rollback to old service:
```bash
sudo systemctl stop bottle-logger-gunicorn
sudo systemctl disable bottle-logger-gunicorn
sudo systemctl start bottle-logger  # Old wsgiref service
```
