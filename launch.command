#!/bin/bash
cd "$(dirname "$0")"

echo "==================================="
echo "  CNC Nest Tool"
echo "==================================="
echo ""

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 is not installed."
    echo ""
    echo "Please download and install it from:"
    echo "  https://python.org"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

# Run from the project's own virtual environment. A Homebrew python3 refuses
# pip installs (PEP 668), and which python3 is first on PATH can change.
PYTHON=".venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
    if [ $? -ne 0 ]; then
        echo ""
        echo "ERROR: Failed to create the virtual environment."
        read -p "Press Enter to exit..."
        exit 1
    fi
fi

# Check / install dependencies (Flask, reportlab, etc.)
if ! "$PYTHON" -c "import flask, reportlab" &>/dev/null; then
    echo "Dependencies not found. Installing..."
    "$PYTHON" -m pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo ""
        echo "ERROR: Failed to install dependencies."
        echo "Try running: .venv/bin/python3 -m pip install -r requirements.txt"
        read -p "Press Enter to exit..."
        exit 1
    fi
    echo ""
fi

# Kill anything already on port 5001
EXISTING_PID=$(lsof -ti tcp:5001 2>/dev/null)
if [ -n "$EXISTING_PID" ]; then
    echo "Stopping existing process on port 5001 (PID $EXISTING_PID)..."
    kill -9 $EXISTING_PID 2>/dev/null
    sleep 0.5
fi

echo "Starting server..."
"$PYTHON" app.py &
SERVER_PID=$!

# Wait up to 10 seconds for server to respond
READY=0
for i in $(seq 1 10); do
    if curl -s http://localhost:5001 >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 1
done

if [ $READY -eq 0 ]; then
    # Check if the process is still running
    if ! kill -0 $SERVER_PID 2>/dev/null; then
        echo ""
        echo "ERROR: Server failed to start. Check the output above for details."
        read -p "Press Enter to exit..."
        exit 1
    fi
    echo "Server is taking longer than expected — opening browser anyway..."
fi

open http://localhost:5001

echo ""
echo "CNC Nest Tool is running at http://localhost:5001"
echo "Close this window to stop the server."
echo ""

wait $SERVER_PID
