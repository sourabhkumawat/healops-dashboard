#!/bin/bash

# Quick start script for running Healops backend locally

set -e

echo "🚀 Starting Healops Backend Locally"
echo "===================================="

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  Warning: .env file not found!"
    echo "   Please create a .env file with required environment variables."
    echo "   See DEBUG_GUIDE.md for details."
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source venv/bin/activate

# Install dependencies if needed
if [ ! -f ".deps_installed" ]; then
    echo "📥 Installing dependencies..."
    pip install -r requirements.txt
    touch .deps_installed
fi

# Check database connection
echo "🔍 Checking database connection..."
python3 -c "
from database import engine
try:
    with engine.connect() as conn:
        print('✅ Database connection successful')
except Exception as e:
    print(f'❌ Database connection failed: {e}')
    print('   Make sure PostgreSQL is running and DATABASE_URL is correct')
    exit(1)
"

# Start the server
echo ""
echo "🌐 Starting FastAPI server on http://localhost:8000"
echo "   Press Ctrl+C to stop"
echo ""
echo "💡 To debug with breakpoints:"
echo "   1. Open VS Code"
echo "   2. Go to Run and Debug (Cmd+Shift+D)"
echo "   3. Select 'Python: FastAPI (Backend)'"
echo "   4. Press F5"
echo ""

uvicorn main:app --host 0.0.0.0 --port 8000 --reload

