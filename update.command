#!/bin/bash
cd "$(dirname "$0")"

echo "==================================="
echo "  CNC Nest Tool — Update"
echo "==================================="
echo ""

# Check git is available
if ! command -v git &>/dev/null; then
    echo "ERROR: Git is not installed."
    echo ""
    echo "Install it by running in Terminal:"
    echo "  xcode-select --install"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

echo "Pulling latest updates from GitHub..."
git pull

if [ $? -eq 0 ]; then
    echo ""
    echo "Update successful! Relaunching app..."
    echo ""
    exec "$(dirname "$0")/launch.command"
else
    echo ""
    echo "ERROR: Update failed. Check your internet connection or the output above."
    read -p "Press Enter to exit..."
    exit 1
fi
