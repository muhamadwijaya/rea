"""Long-term memory of JARVIS on the phone: what the user asked JARVIS to keep, stored in the SHARX data folder.

The file is JSON grouped by category: {"identity": {"name": {"value": "Budi", "updated": "2026-11-21"}}, ...}. A
compact part of it goes into every conversation (identity in full, then the most recently updated facts, then only
the keys of everything else), and recall searches the rest on demand.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import date
from pathlib import Path

CATEGORIES = ('identity', 'preferences', 'projects', 'relationships', 'wishes', 'notes')
MAX_VALUE = 400
MAX_ENTRIES = 2000
PROMPT_CHARS = 1400
_WORD = re.compile(r'[\w]+', re.UNICODE)


class Memory:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()

    def load(self) -> dict:
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            return {c: dict(data.get(c) or {}) for c in sorted(set(CATEGORIES) | set(data))
                    if isinstance(data.get(c, {}), dict)}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(self.path)

    @staticmethod
    def _value(entry) -> str:
        return str(entry.get('value') if isinstance(entry, dict) else entry or '').strip()

    def entries(self) -> list[dict]:
        rows = []
        for category, items in self.load().items():
            for key, entry in items.items():
                value = self._value(entry)
                if value:
                    rows.append(dict(category=category, key=key, value=value,
                                     updated=entry.get('updated') if isinstance(entry, dict) else None))
        return sorted(rows, key=lambda r: (r['updated'] or '', r['category'], r['key']), reverse=True)

    def remember(self, category: str, key: str, value: str) -> None:
        category = category if category in CATEGORIES else 'notes'
        key = re.sub(r'\s+', '_', key.strip().lower())[:60]
        value = ' '.join(value.split())[:MAX_VALUE]
        if not key or not value:
            return
        with self.lock:
            data = self.load()
            data.setdefault(category, {})[key] = {'value': value, 'updated': date.today().isoformat()}
            rows = sum(len(v) for v in data.values())
            if rows > MAX_ENTRIES:  # a runaway loop must not grow the file without end: oldest notes go first
                notes = sorted(data.get('notes', {}).items(), key=lambda kv: kv[1].get('updated', ''))
                for old, _ in notes[:rows - MAX_ENTRIES]:
                    data['notes'].pop(old, None)
            self._save(data)

    def forget(self, category: str, key: str) -> bool:
        with self.lock:
            data = self.load()
            if data.get(category, {}).pop(key, None) is None:
                return False
            self._save(data)
            return True

    def prompt_block(self) -> str:
        rows = self.entries()
        if not rows:
            return ''
        lines, used = [], 0
        identity = [r for r in rows if r['category'] == 'identity']
        others = [r for r in rows if r['category'] != 'identity']
        shown = set()
        for r in identity + others:
            line = f"- {r['category']}/{r['key']}: {r['value']}"
            if r['category'] != 'identity' and used + len(line) > PROMPT_CHARS:
                break
            lines.append(line)
            used += len(line)
            shown.add((r['category'], r['key']))
        hidden = [f"{r['category']}/{r['key']}" for r in rows if (r['category'], r['key']) not in shown]
        block = '[MEMORY]\nWhat the user asked you to keep (facts about them; their current words still decide the language):\n'
        block += '\n'.join(lines)
        if hidden:
            block += '\nAlso stored, not shown here (use recall_memory): ' + ', '.join(hidden[:60])
        return block + '\n'

    def search(self, query: str, limit: int = 8) -> str:
        words = {w for w in _WORD.findall(query.lower()) if len(w) > 1}
        scored = []
        for r in self.entries():
            text = f"{r['category']} {r['key'].replace('_', ' ')} {r['value']}".lower()
            score = sum(3 if w in r['key'] else 1 for w in words if w in text)
            if score or not words:
                scored.append((score, r))
        scored.sort(key=lambda sr: sr[0], reverse=True)
        if not scored:
            return f'Nothing stored matches "{query}".'
        return '\n'.join(f"{r['category']}/{r['key']}: {r['value']}" for _, r in scored[:limit])
