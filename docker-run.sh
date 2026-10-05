#!/bin/bash

echo "🚀 Starting Steirische Linien Development Environment"
echo "=================================================="
echo ""
echo "Building and starting Home Assistant container..."
echo ""

# Build and start the container
docker-compose up --build -d

echo ""
echo "⏳ Waiting for Home Assistant to start (this may take a minute)..."

# Wait for Home Assistant to be ready
max_attempts=30
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:8333 | grep -q "200\|401"; then
        echo ""
        echo "✅ Home Assistant is ready!"
        echo ""
        echo "📋 Setup Instructions:"
        echo "======================================"
        echo "1. Open http://localhost:8333 in your browser"
        echo "2. Create a Home Assistant account (first time only)"
        echo "3. Go to Settings → Devices & Services"
        echo "4. Click '+ Add Integration'"
        echo "5. Search for 'Steiermark Öffis'"
        echo "6. Configure with your API URL and coordinates"
        echo ""
        echo "📊 The dashboard card is already configured and will"
        echo "   show data once the integration is set up!"
        echo ""
        echo "🛑 To stop: docker-compose down"
        echo "📝 To view logs: docker-compose logs -f"
        echo ""
        break
    fi
    attempt=$((attempt + 1))
    echo -n "."
    sleep 2
done

if [ $attempt -eq $max_attempts ]; then
    echo ""
    echo "⚠️  Home Assistant took too long to start."
    echo "Check logs with: docker-compose logs"
fi