#!/usr/bin/env python3
"""
WSGI entry point for production servers (gunicorn, uWSGI, etc.)
This separates the WSGI application from the development server runner.
"""

from logger import (
    get, post, route,
    save_log, get_logs, get_frontend_config, frontend_config_options
)
from bottle import default_app

# Create the WSGI application
app = default_app()

# The routes are already registered via decorators in logger.py
# Gunicorn will use this 'app' object

if __name__ == "__main__":
    # This won't run under gunicorn, but allows testing
    from bottle import run
    run(app=app, host='127.0.0.1', port=8082, debug=False)
