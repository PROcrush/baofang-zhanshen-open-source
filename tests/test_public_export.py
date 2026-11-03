import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.export_public_source import export_source


class PublicExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)/'source'
        self.root.mkdir()
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        for name, text in {
            'app/main.py': 'print("synthetic source")\n',
            'runtime/private.json': '{"secret":"not public"}',
            'HANDOFF.md': 'private notes',
            'probes/latest-serato-report.json': '{"private":"report"}',
            'docs/百炼真实歌曲复用实测-20261023.md': 'private cloud receipt',
            'docs/open-source/README.md': '# Public\n[internal](HANDOFF.md)\n',
            'docs/open-source/AGENTS.md': '# Public instructions\n',
            'docs/open-source/PROBES.md': '# Synthetic probe guide\n',
            'LICENSE': 'MIT fixture',
        }.items():
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
        subprocess.run(['git','add','.'],cwd=self.root,check=True)
        subprocess.run(['git','-c','user.name=Fixture','-c','user.email=fixture@example.invalid',
                        'commit','-qm','Synthetic fixture'],cwd=self.root,check=True)
        self.destination=Path(self.temp.name)/'public'

    def tearDown(self):
        self.temp.cleanup()

    def test_public_snapshot_excludes_runtime_history_reports_and_private_links(self):
        export_source(self.root,self.destination)
        self.assertTrue((self.destination/'app/main.py').exists())
        self.assertTrue((self.destination/'SOURCE_SNAPSHOT.json').exists())
        for name in ('.git','runtime','HANDOFF.md','probes/latest-serato-report.json',
                     'docs/百炼真实歌曲复用实测-20261023.md'):
            self.assertFalse((self.destination/name).exists(),name)
        self.assertNotIn('(HANDOFF.md)',(self.destination/'README.md').read_text())

    def test_export_never_overwrites_existing_data(self):
        self.destination.mkdir()
        (self.destination/'keep').write_text('keep')
        with self.assertRaises(ValueError):export_source(self.root,self.destination)
        self.assertEqual((self.destination/'keep').read_text(),'keep')

    def test_tracked_symlink_cannot_export_private_external_file(self):
        outside=Path(self.temp.name)/'private-key'
        outside.write_text('private fixture')
        (self.root/'app/link.py').symlink_to(outside)
        subprocess.run(['git','add','app/link.py'],cwd=self.root,check=True)
        with self.assertRaises(ValueError):export_source(self.root,self.destination)
        self.assertFalse(self.destination.exists())


if __name__=='__main__':unittest.main()
