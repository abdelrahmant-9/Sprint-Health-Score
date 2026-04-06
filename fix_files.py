#!/usr/bin/env python3
"""
Run this ONCE to fix mojibake encoding in all sprint_health_*.py files.
Usage: python3 fix_files.py
"""
import os


def fix_mojibake(text: str) -> str:
    """Reverse mojibake: chars that were UTF-8 bytes mis-read as Latin-1."""
    result = []
    i = 0
    chars = list(text)
    while i < len(chars):
        c = chars[i]
        try:
            seq = c.encode('latin-1')
            if seq[0] >= 0x80:
                j = i + 1
                while j < len(chars) and len(seq) < 6:
                    try:
                        nb = chars[j].encode('latin-1')
                        if nb[0] >= 0x80:
                            seq += nb
                            j += 1
                        else:
                            break
                    except Exception:
                        break
                try:
                    result.append(seq.decode('utf-8'))
                    i = j
                    continue
                except (UnicodeDecodeError, ValueError):
                    pass
            result.append(c)
            i += 1
        except (UnicodeEncodeError, ValueError):
            result.append(c)
            i += 1
    return ''.join(result)


FILES = [
    'sprint_health_2.py',
    'sprint_health_scoring.py',
    'sprint_health_config.py',
    'admin_dashboard.py',
]

for fname in FILES:
    if not os.path.exists(fname):
        print(f'[skip] {fname} not found')
        continue
    with open(fname, 'r', encoding='utf-8', errors='replace') as fh:
        original = fh.read()
    fixed = fix_mojibake(original)
    if fixed == original:
        print(f'[ok]    {fname} — no encoding issues')
        continue
    changed = sum(1 for a, b in zip(original, fixed) if a != b)
    backup = fname + '.bak'
    with open(backup, 'w', encoding='utf-8') as fh:
        fh.write(original)
    with open(fname, 'w', encoding='utf-8') as fh:
        fh.write(fixed)
    print(f'[fixed] {fname} — {changed} chars corrected  (backup: {backup})')

print('Done!')