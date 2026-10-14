#!/usr/bin/env bash
# Deploy the current git-tracked tree to prod (EC2) in one shot.
#
# Why this exists: every deploy before this was an ad-hoc `scp` of whatever
# files someone remembered to list. That silently missed whole commits more
# than once (see memory: sitewide-bug-sweep-oct6, gk-ratings-deploy-gap-oct13)
# -- a feature could sit fully committed, fully working locally, and entirely
# absent from prod for months with nothing surfacing it until a user hit the
# gap. This rsyncs the EXACT file list `git ls-files` returns, so "did I
# remember every file" stops being a question anyone has to get right by hand.
#
# What it does NOT touch: the warehouse (data/), the venv, backups/, logs --
# none of that is git-tracked, so none of it is in the --files-from list, so
# rsync never looks at it. Safe to run as often as you want.
#
# Usage: ./deploy.sh              # deploy + restart + smoke test
#        ./deploy.sh --no-restart # sync files only, leave the service running
#                                  # on old code (rare -- only if you're about
#                                  # to run a one-off script with these files
#                                  # before restarting yourself)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

HOST="ubuntu@16.59.15.84"
KEY="$HOME/.ssh/atlastra-ec2.pem"
REMOTE_DIR="/opt/atlastra"
RESTART=1
[[ "${1:-}" == "--no-restart" ]] && RESTART=0

SHA=$(git rev-parse HEAD)
SHORT=$(git rev-parse --short HEAD)
DIRTY=""
git diff --quiet || DIRTY=" (dirty working tree!)"

echo "==> Deploying $SHORT$DIRTY"
if [[ -n "$DIRTY" ]]; then
  echo "    Uncommitted local changes will be included in this deploy but"
  echo "    NOT recorded in .deployed_sha -- commit first if that matters."
fi

echo "==> Syncing $(git ls-files | wc -l | tr -d ' ') tracked files..."
git ls-files > /tmp/.atlastra_deploy_files
rsync -az --files-from=/tmp/.atlastra_deploy_files -e "ssh -i $KEY" \
  ./ "$HOST:$REMOTE_DIR/"
rm -f /tmp/.atlastra_deploy_files

echo "==> Recording deployed SHA..."
ssh -i "$KEY" "$HOST" "printf '%s\n%s\n' '$SHA' \"\$(date -u +%FT%TZ)\" > $REMOTE_DIR/.deployed_sha"

if [[ "$RESTART" -eq 1 ]]; then
  echo "==> Restarting service..."
  ssh -i "$KEY" "$HOST" "sudo systemctl restart atlastra"
  echo "==> Waiting for it to come back..."
  for i in $(seq 1 20); do
    sleep 3
    CODE=$(curl -s -o /dev/null -w "%{http_code}" https://atlastra.dedyn.io/ || echo "000")
    if [[ "$CODE" == "200" ]]; then
      echo "    up after ${i}x3s"
      break
    fi
    if [[ "$i" -eq 20 ]]; then
      echo "!!  Still not responding (last code: $CODE) after 60s -- check logs:"
      echo "    ssh -i $KEY $HOST 'sudo journalctl -u atlastra -n 50 --no-pager'"
      exit 1
    fi
  done

  echo "==> Smoke test..."
  FAIL=0
  for check in \
    "GET / -> 200|curl -s -o /dev/null -w '%{http_code}' https://atlastra.dedyn.io/" \
    "GET /api/live -> valid JSON|curl -s https://atlastra.dedyn.io/api/live | python3 -c 'import json,sys; d=json.load(sys.stdin); assert \"live\" in d and \"upcoming\" in d'" \
    "GET /api/player -> valid JSON|curl -s 'https://atlastra.dedyn.io/api/player?name=Pedri' | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get(\"name\")'" \
  ; do
    name="${check%%|*}"; cmd="${check#*|}"
    if out=$(eval "$cmd" 2>&1) && { [[ "$name" == *"200"* && "$out" == "200" ]] || [[ "$name" != *"200"* ]]; }; then
      echo "    OK  $name"
    else
      echo "    FAIL  $name  ($out)"
      FAIL=1
    fi
  done
  if [[ "$FAIL" -eq 1 ]]; then
    echo "!!  Smoke test failed -- deploy completed but something's off. Check logs."
    exit 1
  fi
else
  echo "==> Skipped restart (--no-restart). Service is running old code until you restart it."
fi

echo "==> Done. Deployed $SHORT."
