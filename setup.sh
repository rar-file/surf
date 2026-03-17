#!/bin/bash
# ============================================================
# 🚀 AI Web Search Skill - Quick Setup Script
# ============================================================
# Run this script to install everything you need!
# Usage: chmod +x setup.sh && ./setup.sh
# ============================================================

echo "🔧 Setting up AI Web Search Skill..."
echo ""

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is required but not installed."
    echo "   Please install Python from: https://python.org"
    exit 1
fi

echo "✅ Python found: $(python3 --version)"

# Create virtual environment
echo ""
echo "📦 Creating virtual environment..."
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Install dependencies
echo ""
echo "📥 Installing dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# Install Playwright browsers
echo ""
echo "🌐 Installing browser (Chromium)..."
playwright install chromium

echo ""
echo "============================================================"
echo "✅ Setup Complete!"
echo "============================================================"
echo ""
echo "To use the search skill:"
echo ""
echo "1. Activate the environment:"
echo "   source venv/bin/activate"
echo ""
echo "2. Run SURF:"
echo "   python chat.py"
echo ""
echo "3. Or launch the web UI:"
echo "   python -m core.web_ui"
echo ""
echo "============================================================"
