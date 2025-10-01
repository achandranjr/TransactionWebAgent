#!/bin/bash
set -e

echo "Starting Claude Playwright Agent Container..."

# Create necessary directories
mkdir -p /app/static  /var/log/supervisor

# Copy dashboard to static directory for serving
if [ -f /app/dashboard.html ]; then
    cp /app/dashboard.html /app/static/dashboard.html
fi


# Start supervisor to manage all services

exec /usr/local/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf