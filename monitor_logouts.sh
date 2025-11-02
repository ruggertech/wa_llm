#!/bin/bash
# Monitor WhatsApp logout events and connection issues
# Usage: ./monitor_logouts.sh

echo "🔍 Monitoring WhatsApp logout events..."
echo "Press Ctrl+C to stop"
echo ""
echo "Watching for:"
echo "  - REMOTE_LOGOUT events"
echo "  - Keep-alive failures"
echo "  - Connection errors"
echo "  - Reconnection attempts"
echo ""

# Follow logs from both services
docker compose logs -f --tail 50 whatsapp web-server 2>&1 | \
  grep --line-buffered -iE "REMOTE_LOGOUT|logout|keep.*alive|ping.*successful|401|disconnect|reconnect|session.*expired|you are not connect" | \
  sed 's/^/📱 /'

