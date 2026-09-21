#!/bin/sh
set -eu
python -m unittest discover -v tests
python run_experiments.py --seeds "${SEEDS:-30}" --outdir results
python make_final_numbers.py
if [ ! -f FINAL-NUMBERS-FROZEN.json ]; then
  cp FINAL-NUMBERS.json FINAL-NUMBERS-FROZEN.json
  echo "Initialized FINAL-NUMBERS-FROZEN.json"
fi
python check_consistency.py FINAL-NUMBERS-FROZEN.json FINAL-NUMBERS.json
