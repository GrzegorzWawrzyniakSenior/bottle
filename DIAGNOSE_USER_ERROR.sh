#!/bin/bash
# Diagnose the 217/USER error

echo "=== 1. Check if user 'boss' exists ==="
id boss 2>&1

echo ""
echo "=== 2. Check who owns /var/grono/bottle ==="
ls -ld /var/grono/bottle

echo ""
echo "=== 3. Check who owns the venv ==="
ls -ld /var/grono/bottle/venv

echo ""
echo "=== 4. Check current running logger service user ==="
ps aux | grep -E "(logger.py|python.*logger)" | grep -v grep

echo ""
echo "=== 5. Check systemd journal for more details ==="
sudo journalctl -u bottle-logger-gunicorn.service -n 20 --no-pager
