#!/bin/bash

echo "🔍 Monitoring Logfire & Voyage Activity"
echo "=========================================="
echo ""
echo "Environment Check:"
docker compose exec web-server env | grep -E "LOGFIRE_TOKEN|VOYAGE_API_KEY" | sed 's/=.*/=***/' || echo "⚠️ Container not running"
echo ""
echo "Recent Logfire Activity:"
docker compose logs --tail 100 web-server 2>&1 | grep -iE "logfire|LogfireNotConfigured" | tail -5 || echo "  (No logfire activity in recent logs)"
echo ""
echo "Recent Voyage Activity:"
docker compose logs --tail 100 web-server 2>&1 | grep -iE "voyage|embed|knowledge.*base|RAG|topic.*embed" | tail -5 || echo "  (No voyage activity in recent logs - normal if no KB operations)"
echo ""
echo "📊 Live Monitoring (Ctrl+C to stop):"
echo "Watching for: logfire, voyage, embed, knowledge, RAG"
echo ""
docker compose logs -f web-server 2>&1 | grep --line-buffered -iE "logfire|voyage|embed|knowledge.*base|RAG|topic.*embed|voyage_embed"

