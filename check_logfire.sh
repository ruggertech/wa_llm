#!/bin/bash

echo "🔍 Checking Logfire Configuration..."
echo "======================================"
echo ""

# Check environment variable
echo "1. Environment Variable:"
docker compose exec web-server env | grep LOGFIRE_TOKEN | sed 's/=.*/=***/' || echo "  ❌ LOGFIRE_TOKEN not set"
echo ""

# Check if logfire is imported and configured
echo "2. Logfire Import & Configuration:"
docker compose exec web-server python -c "
import logfire
import os
print('  ✅ Logfire imported successfully')
print('  LOGFIRE_TOKEN:', 'SET' if os.getenv('LOGFIRE_TOKEN') else 'NOT SET')
try:
    logfire.configure()
    print('  ✅ logfire.configure() called (no errors)')
except Exception as e:
    print(f'  ❌ Error: {e}')
" 2>&1 | grep -v "Traceback\|File\|Error checking"
echo ""

# Check for errors in logs
echo "3. Recent Logfire Errors/Warnings:"
docker compose logs --tail 500 web-server 2>&1 | grep -iE "logfire.*error|logfire.*warning|LogfireNotConfigured" | tail -5 || echo "  ✅ No errors found"
echo ""

# Make a test API call
echo "4. Making test API call (should be logged to Logfire):"
curl -s http://localhost:8000/status > /dev/null && echo "  ✅ API call successful" || echo "  ❌ API call failed"
echo ""

echo "======================================"
echo "📊 Next Steps:"
echo "1. Go to https://logfire.pydantic.dev/"
echo "2. Log in and check your project"
echo "3. Look for recent logs/spans from the last few minutes"
echo "4. You should see HTTP requests, DB queries, etc."
echo ""
