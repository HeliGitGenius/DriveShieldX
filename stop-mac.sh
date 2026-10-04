#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ -f dashboard.pid ]; then
  kill "$(cat dashboard.pid)" 2>/dev/null && echo "stopped dashboard"
  rm -f dashboard.pid
else
  pkill -f "streamlit run dashboard/app.py" 2>/dev/null && echo "stopped dashboard"
fi
