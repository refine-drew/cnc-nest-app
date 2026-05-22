#!/bin/bash
cd "$(dirname "$0")"
echo "Updating CNC Nest Tool..."
git pull
echo "Starting server..."
python3 app.py &
sleep 2
open http://localhost:5000
wait
