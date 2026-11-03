"""Flag library rows that look like the same song imported twice.

Same audio bytes already share one version id. This catches the other case:
copies with a different file (re-encoded, renamed copy) whose cleaned title
matches and whose length is within one second. Different remixes usually
differ in length, so they are not flagged. Only a hint; nothing is deleted.
"""
import re
import unicodedata

_NOISE = re.compile(r'[\s\-_·.,，。()（）\[\]【】]+|(?:副本|copy|拷贝)\s*\d*$', re.IGNORECASE)


def title_key(title):
    text = unicodedata.normalize('NFKC', title or '').lower()
    text = re.sub(r'\s*\(\d+\)$', '', text)
    return _NOISE.sub('', text)


def mark_duplicates(rows, tolerance_ms=1000):
    """Set 'duplicateCount' (other look-alike copies) on each row; returns groups found."""
    groups = {}
    for row in rows:
        key = title_key(row.get('title'))
        if key and row.get('durationMs'):
            groups.setdefault(key, []).append(row)
    found = 0
    for row in rows:
        row['duplicateCount'] = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        for row in members:
            same = [o for o in members if o is not row and o.get('versionId') != row.get('versionId')
                    and abs(o['durationMs'] - row['durationMs']) <= tolerance_ms]
            row['duplicateCount'] = len(same)
        found += any(r['duplicateCount'] for r in members)
    return found
