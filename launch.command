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

# Check / install Flask
if ! python3 -c "import flask" &>/dev/null; then
    echo "Flask not found. Installing..."
    pip3 install flask
    if [ $? -ne 0 ]; then
        echo ""
        echo "ERROR: Failed to install Flask."
        echo "Try running: pip3 install flask"
        read -p "Press Enter to exit..."
        exit 1
    fi
    echo ""
fi

echo "Starting server..."
python3 app.py &
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
