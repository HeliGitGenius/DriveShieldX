#!/usr/bin/env bash
# DriveShieldX — one-command boot for macOS / Linux
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"

echo ""
echo "======================================================="
echo "  DriveShieldX — starting local development stack      "
echo "======================================================="
echo ""

[ -f .env ] || cp .env.example .env

echo "[1/3] Preparing Python virtual environment…"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip

echo "[2/3] Installing Python dependencies (a few minutes on first run)…"
pip install --quiet -r requirements.txt

# ensure sqlite schema is applied
python3 -c "from database.db_manager import init_database; init_database()"

echo "[3/3] Launching Streamlit dashboard on http://localhost:8501 …"
# load .env safely (values may contain spaces; Windows line endings are fine)
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"
  case "$line" in ''|\#*) continue ;; esac
  key="${line%%=*}"; val="${line#*=}"
  key="${key//[[:space:]]/}"
  case "$key" in ''|*[!A-Za-z0-9_]*) continue ;; esac
  val="${val%\"}"; val="${val#\"}"
  export "$key=$val"
done < .env
nohup streamlit run dashboard/app.py \
  --server.port 8501 --server.address 0.0.0.0 --server.headless true \
  --browser.gatherUsageStats false > dashboard.log 2>&1 &
echo $! > dashboard.pid

sleep 8
(command -v open >/dev/null && open http://localhost:8501) || true
echo ""
echo "======================================================="
echo "  DriveShieldX is booting."
echo "  Open  http://localhost:8501"
echo ""
echo "  Sign in (demo):"
echo "     Admin             : admin@speedcam.com    / admin123"
echo "     Traffic officer   : officer@speedcam.com  / officer123"
echo "     Vehicle owner     : use 'Sign up as vehicle owner' on the first page"
echo ""
echo "  Logs:  tail -f $ROOT/dashboard.log"
echo "  Stop:  ./stop-mac.sh"
echo "======================================================="
