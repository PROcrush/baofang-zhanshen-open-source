"""A small local recent-project list; opening still verifies every source identity."""
import json
import uuid
from pathlib import Path


class RecentProjects:
    limit = 6

    def __init__(self, runtime):
        self.path = Path(runtime) / 'studio-recent-projects.json'
        self.paths = []
        try:
            if self.path.stat().st_size <= 65536:
                values = json.loads(self.path.read_text(encoding='utf-8'))
                if isinstance(values, list):
                    for value in values:
                        if (isinstance(value, str) and len(value) <= 4096
                                and Path(value).is_absolute() and value.endswith('.json')
                                and value not in self.paths):
                            self.paths.append(value)
                            if len(self.paths) == self.limit:
                                break
        except (OSError, ValueError, TypeError):
            pass

    def rows(self):
        return [{'path': value, 'title': Path(value).name.removesuffix('.bfstudio.json'),
                 'available': Path(value).is_file()} for value in self.paths]

    def remember(self, path):
        value = str(path)
        self.paths = ([value] + [row for row in self.paths if row != value])[:self.limit]
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(self.paths, ensure_ascii=False), encoding='utf-8')
            temporary.replace(self.path)
        except OSError:
            pass  # A preference write must never turn a saved project into a failed save.
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
