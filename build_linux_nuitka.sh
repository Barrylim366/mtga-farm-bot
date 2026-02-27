#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "[1/4] Checking Nuitka installation..."
if ! python3 -m pip show nuitka >/dev/null 2>&1; then
  echo "Nuitka not found. Installing Nuitka build dependencies..."
  python3 -m pip install nuitka ordered-set zstandard
fi

echo "[2/4] Preparing data include arguments..."
DATA_ARGS=(
  --include-data-dir=images=images
  --include-data-dir=Buttons=Buttons
  --include-data-files=cards.json=cards.json
  --include-data-files=cards_metadata.json=cards_metadata.json
  --include-data-files=missing_cards.json=missing_cards.json
  --include-data-files=scryfall_cache.json=scryfall_cache.json
  --include-data-files=scryfall_oracle_cache.json=scryfall_oracle_cache.json
  --include-data-files=scryfall_bulk_metadata.json=scryfall_bulk_metadata.json
  --include-data-files=calibration_config.json=calibration_config.json
  --include-data-files=config/public_key.jwk=config/public_key.jwk
)
if [[ -f recorded_actions_records.json ]]; then
  DATA_ARGS+=(--include-data-files=recorded_actions_records.json=recorded_actions_records.json)
else
  echo "Note: recorded_actions_records.json not found, skipping include."
fi

echo "[3/4] Building BurningLotusBot with Nuitka (standalone)..."
python3 -m nuitka \
  --standalone \
  --assume-yes-for-downloads \
  --enable-plugin=tk-inter \
  --output-dir=dist_nuitka \
  --output-filename=BurningLotusBot \
  "${DATA_ARGS[@]}" \
  ui.py

echo "[4/4] Build complete."
echo "Output folder: dist_nuitka/ui.dist"
echo
echo "Run from inside folder:"
echo "  ./dist_nuitka/ui.dist/BurningLotusBot"

