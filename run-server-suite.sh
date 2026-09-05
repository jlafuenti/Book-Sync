#!/bin/bash
# Run the whole server suite as 3 parallel chunks (recomputed every run).
cd "$(dirname "$0")/server"
python - <<'PY'
import glob
files=sorted(glob.glob('tests/test_*.py'))
for i in range(3):
    open(f'../chunk{i}.txt','w').write(' '.join(files[i::3]))
print(f"{len(files)} test files")
PY
for i in 0 1 2; do
  ( .venv/Scripts/python.exe -m pytest -q $(cat ../chunk$i.txt) -p no:cacheprovider > ../server-chunk$i.log 2>&1; echo "chunk$i exit=$?" >> ../server-chunk$i.log ) &
done
wait
for i in 0 1 2; do echo "== chunk$i: $(tail -n 1 ../server-chunk$i.log) | $(grep -E 'passed|failed' ../server-chunk$i.log | tail -n 1)"; done
grep -hE "^FAILED|^ERROR" ../server-chunk*.log | head -20
