# URGENT: Fix 504 Gateway Timeout - Deploy Gunicorn

## Problem
Your production server is using the **development server** (QuietWSGIRefServer) which can't handle concurrent requests, causing 504 timeouts.

## Solution Steps (Execute in order)

### 1. Install Gunicorn in Production Environment
```bash
source /var/grono/bottle/venv/bin/activate
pip install gunicorn
```

### 2. Copy Updated Files to Production
```bash
# Copy the fixed logger.py (deprecation warnings fixed)
sudo cp /home/boss/workspace/bottle/logger.py /var/grono/bottle/logger.py
sudo chown boss:boss /var/grono/bottle/logger.py

# Copy the new service file
sudo cp /home/boss/workspace/bottle/bottle-logger-gunicorn-production.service /etc/systemd/system/bottle-logger-gunicorn.service
```

### 3. Stop Old Service and Start New One
```bash
# Stop the development server
sudo systemctl stop bottle-logger.service
sudo systemctl disable bottle-logger.service

# Reload systemd daemon to recognize new service
sudo systemctl daemon-reload

# Start and enable the gunicorn service
sudo systemctl start bottle-logger-gunicorn.service
sudo systemctl enable bottle-logger-gunicorn.service

# Check status
sudo systemctl status bottle-logger-gunicorn.service
```

### 4. Verify It's Working
```bash
# Watch logs in real-time
journalctl -u bottle-logger-gunicorn.service -f

# Test the endpoint
curl -v http://127.0.0.1:8082/frontend-config

# From external (should work now without 504)
curl https://zgranegrono.pl/frontend-config
```

### 5. Monitor Performance
```bash
# Check worker processes
ps aux | grep gunicorn

# Should show:
# - 1 master process
# - 4 worker processes (or your configured number)
```

## What Changed

### Code Fixes ([logger.py](logger.py))
- ✅ Fixed `datetime.utcnow()` deprecation warnings
- ✅ Now using `datetime.now(timezone.utc)` (Python 3.12+ compatible)

### Infrastructure Upgrade
- ✅ **Before**: Single-threaded development server (QuietWSGIRefServer)
- ✅ **After**: Production-ready Gunicorn with 4 worker processes
- ✅ Can handle concurrent requests efficiently
- ✅ Automatic worker recycling (max-requests)
- ✅ Better timeout handling

### Performance Improvements
- **Request capacity**: 1 concurrent → 40+ concurrent
- **Timeout handling**: Built-in worker health checks
- **Memory**: Automatic worker recycling prevents memory leaks
- **Stability**: Workers restart automatically on crashes

## Rollback (If Needed)
```bash
# If something goes wrong, revert to old service:
sudo systemctl stop bottle-logger-gunicorn.service
sudo systemctl start bottle-logger.service
```

## Nginx Configuration (Already Correct)
Your nginx should proxy to `127.0.0.1:8082` which is what gunicorn binds to.

If you still see timeouts after deploying, increase nginx timeouts:
```nginx
location /frontend-config {
    proxy_pass http://127.0.0.1:8082;
    proxy_connect_timeout 10s;
    proxy_send_timeout 10s;
    proxy_read_timeout 10s;  # Increase if needed
}
```

Then reload nginx:
```bash
sudo nginx -t
sudo systemctl reload nginx
```

## Expected Results
- ✅ No more 504 Gateway Timeout errors
- ✅ Faster response times
- ✅ Can handle traffic spikes
- ✅ No deprecation warnings in logs
