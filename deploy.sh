#!/bin/bash
# Quick deployment script for production setup

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================="
echo "Bottle Logger Production Deployment"
echo "========================================="
echo ""

# Check if running as root (should not)
if [ "$EUID" -eq 0 ]; then 
    echo "❌ Do not run this script as root!"
    echo "   Run as regular user, will use sudo when needed"
    exit 1
fi

# Step 1: Install gunicorn
echo "📦 Step 1: Installing gunicorn..."
if [ ! -f "venv/bin/activate" ]; then
    echo "❌ Virtual environment not found at venv/"
    echo "   Create it first: python3 -m venv venv"
    exit 1
fi

source venv/bin/activate
pip install -q gunicorn
echo "✅ Gunicorn installed"
echo ""

# Step 2: Test the application
echo "🧪 Step 2: Testing application..."
python logger.py &
APP_PID=$!
sleep 2

if curl -s http://127.0.0.1:8082/frontend-config > /dev/null; then
    echo "✅ Application responds correctly"
else
    echo "❌ Application test failed!"
    kill $APP_PID 2>/dev/null || true
    exit 1
fi

kill $APP_PID
echo ""

# Step 3: Install systemd service
echo "🔧 Step 3: Installing systemd service..."
if [ ! -f "bottle-logger-gunicorn.service" ]; then
    echo "❌ Service file not found!"
    exit 1
fi

# Stop old service if exists
sudo systemctl stop bottle-logger 2>/dev/null || true
sudo systemctl stop bottle-logger-gunicorn 2>/dev/null || true

# Copy new service
sudo cp bottle-logger-gunicorn.service /etc/systemd/system/
sudo systemctl daemon-reload
echo "✅ Service installed"
echo ""

# Step 4: Start and enable service
echo "🚀 Step 4: Starting service..."
sudo systemctl start bottle-logger-gunicorn
sudo systemctl enable bottle-logger-gunicorn
sleep 2

# Check status
if sudo systemctl is-active --quiet bottle-logger-gunicorn; then
    echo "✅ Service is running"
else
    echo "❌ Service failed to start!"
    echo ""
    echo "Check logs with:"
    echo "  sudo journalctl -u bottle-logger-gunicorn.service -n 50"
    exit 1
fi
echo ""

# Step 5: Test the service
echo "🧪 Step 5: Testing production service..."
sleep 1
if curl -s http://127.0.0.1:8082/frontend-config > /dev/null; then
    echo "✅ Production service responds correctly"
else
    echo "⚠️  Service running but not responding yet, check logs"
fi
echo ""

# Step 6: Show nginx configuration hint
echo "========================================="
echo "✅ Deployment Complete!"
echo "========================================="
echo ""
echo "📋 Next Steps:"
echo ""
echo "1. Update your nginx configuration:"
echo "   - Edit: sudo nano /etc/nginx/sites-available/zgranegrono.pl"
echo "   - Reference: $SCRIPT_DIR/nginx-example.conf"
echo "   - KEY FIX: Change upstream from 0.0.0.0:8082 to 127.0.0.1:8082"
echo ""
echo "2. Test nginx config:"
echo "   sudo nginx -t"
echo ""
echo "3. Reload nginx:"
echo "   sudo systemctl reload nginx"
echo ""
echo "4. Monitor logs:"
echo "   sudo journalctl -u bottle-logger-gunicorn.service -f"
echo ""
echo "5. Check service status:"
echo "   sudo systemctl status bottle-logger-gunicorn"
echo ""
echo "📊 Performance Monitoring:"
echo "   - Nginx logs: tail -f /var/log/nginx/access.log | grep frontend-config"
echo "   - Service logs: journalctl -u bottle-logger-gunicorn.service -f"
echo "   - Test endpoint: curl http://127.0.0.1:8082/frontend-config"
echo ""
echo "For more details, see: $SCRIPT_DIR/PRODUCTION_SETUP.md"
