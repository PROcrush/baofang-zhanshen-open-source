"""Exercise our actual native JSON writer across macOS FIFO boundaries."""
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import unittest

from probes.rekordbox_ax import RekordboxAXReader


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('clang'), 'macOS native transport')
class NativeAXTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        folder = Path(cls.build.name)
        native = Path(__file__).resolve().parents[1] / 'probes/native/rekordbox_ax.m'
        source = folder / 'writer.m'
        source.write_text('#define main reader_main\n#include ' + json.dumps(str(native), ensure_ascii=False) +
                         '\n#undef main\nint main(void) { @autoreleasepool {\n'
                         'parentPID = getppid(); ownerIdentity(YES);\n'
                         'NSString *text = [@"x" stringByPaddingToLength:80000 withString:@"x" startingAtIndex:0];\n'
                         'return emitJSON(@{@"payload": text}) ? 0 : 2;\n} }\n')
        cls.binary = folder / 'writer'
        subprocess.run(['clang', '-fobjc-arc', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-framework', 'Foundation', '-framework', 'ApplicationServices',
                        '-DREKORDBOX_TARGET_PATH="/unused-test-target"', str(source),
                        '-o', str(cls.binary)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def run_writer(self, consume):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'response.fifo'
            os.mkfifo(path, 0o600)
            stream = os.fdopen(os.open(path, os.O_RDWR | os.O_NONBLOCK), 'rb', buffering=0)
            output = os.open(path, os.O_WRONLY)
            child = subprocess.Popen([str(self.binary)], stdout=output)
            os.close(output)
            reader = RekordboxAXReader()
            reader.stdout = stream
            reader.selector = selectors.DefaultSelector()
            reader.selector.register(stream, selectors.EVENT_READ)
            try:
                if consume:
                    value = reader._line()
                    self.assertEqual(value, {'payload': 'x' * 80000})
                    self.assertEqual(child.wait(timeout=1), 0)
                else:
                    # LaunchServices opens FIFO stdout read/write. Even with a
                    # reader descriptor still alive, a stalled consumer must
                    # never leave an unkillable blocking write behind.
                    self.assertEqual(child.wait(timeout=2), 2)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                reader.selector.close()
                stream.close()

    def test_full_native_snapshot_larger_than_fifo_capacity_arrives(self):
        self.run_writer(True)

    def test_non_consuming_host_bounds_native_write_time(self):
        self.run_writer(False)


if __name__ == '__main__':
    unittest.main()
