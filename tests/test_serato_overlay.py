"""Output routes and safe window-following do not alter DJ playback."""
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import patch

from app.controller import Controller, initial_state
from app.serato_overlay import SeratoOverlay, select_host, visible_regions


PRODUCER = {'pid': 20, 'executable': '/Applications/Serato DJ Pro.app/Contents/MacOS/Serato Video.app/Contents/MacOS/Serato Video'}
MAIN_PRODUCER = {'pid': 19, 'executable': '/Applications/Serato DJ Pro.app/Contents/MacOS/Serato DJ Pro'}
PRODUCERS = [MAIN_PRODUCER, PRODUCER]


def host(**changes):
    row = {'windowID': 30, 'pid': PRODUCER['pid'], 'executablePath': PRODUCER['executable'],
           'bundleID': 'com.serato.seratovideo', 'serato': True, 'onScreen': True, 'layer': 3,
           'title': 'Serato Video Output', 'bounds': {'x': 100, 'y': 100, 'width': 400, 'height': 300}}
    row.update(changes)
    return row


def snapshot(*windows):
    return {'available': True, 'namesAvailable': True, 'windows': list(windows)}


class OverlayRouteTests(unittest.TestCase):
    def test_requested_serato_output_launches_transparent_host_renderer(self):
        with tempfile.TemporaryDirectory() as folder:
            state = initial_state()
            state['outputDestination'] = 'serato_overlay'
            harness = SimpleNamespace(_state=state, output=None, runtime=Path(folder), root=Path.cwd(),
                                      tick=lambda: None, notice=lambda _: None, shutting_down=False,
                                      stateChanged=SimpleNamespace(emit=lambda: None))
            harness._start_output = MethodType(Controller._start_output, harness)
            with patch('app.controller.subprocess.Popen') as launch:
                Controller.openOutput(harness)
            arguments = launch.call_args.args[0]
            self.assertIn('--serato-overlay', arguments,
                          'Serato output request still launches the separate opaque guest window')

    def test_windows_require_exact_output_title_process_and_visible_unique_target(self):
        good = host()
        self.assertEqual(select_host(snapshot(good), PRODUCERS)[0], good)
        for bad in (host(title='Serato DJ Pro'), host(pid=99), host(executablePath='/tmp/Serato Video'), host(onScreen=False),
                    host(bounds={'x':0,'y':0,'width':float('nan'),'height':100}), host(layer=8)):
            with self.subTest(bad=bad):
                self.assertIsNone(select_host(snapshot(bad), PRODUCERS)[0])
        self.assertIsNone(select_host(snapshot(good), [])[0])
        self.assertIsNone(select_host(snapshot(good, host(windowID=31)), PRODUCERS)[0])

    def test_titleless_video_window_uses_verified_nested_producer_identity(self):
        row = host()
        for key in ('title', 'bundleID', 'executablePath'):
            row.pop(key)
        row['serato'] = False  # macOS may withhold application metadata as well.
        state = snapshot(row)
        state['namesAvailable'] = False
        self.assertEqual(select_host(state, PRODUCERS)[0], row)
        self.assertIsNone(select_host(state, [PRODUCER])[0])
        wrong_pair = [MAIN_PRODUCER, dict(PRODUCER, executable='/tmp/Serato Video')]
        self.assertIsNone(select_host(state, wrong_pair)[0])
        row['pid'] = MAIN_PRODUCER['pid']
        row['layer'] = 0
        self.assertIsNone(select_host(state, PRODUCERS)[0], 'A titleless main console must never be selected')

    def test_hidden_output_reports_open_window_instead_of_missing_title_permission(self):
        state = snapshot(host(title='', onScreen=False))
        state['namesAvailable'] = False
        selected, detail = select_host(state, PRODUCERS)
        self.assertIsNone(selected)
        self.assertIn('打开', detail)
        self.assertNotIn('屏幕录制权限', detail)

    def test_occlusion_excludes_own_panel_and_keeps_only_exposed_host_area(self):
        target = host()
        own = host(windowID=31)
        cover = {'windowID': 99, 'pid': 42, 'onScreen': True, 'alpha': 1, 'layer': 3,
                 'bounds': {'x': 100, 'y':100, 'width':200, 'height':300}}
        self.assertEqual(visible_regions([own,target], target, [31]), [(100,100,400,300)])
        self.assertEqual(visible_regions([cover,own,target], target, [31]), [(300,100,200,300)])
        cover['bounds']['width'] = 400
        self.assertEqual(visible_regions([cover,target], target), [])
        self.assertEqual(visible_regions([], target), [])

    def test_windowed_video_is_not_falsely_covered_by_higher_transparent_dock_container(self):
        target = host(bounds={'x':981,'y':263,'width':400,'height':400})
        dock = {'windowID':7011, 'pid':15576, 'layer':20, 'alpha':1, 'onScreen':True,
                'bounds':{'x':0,'y':0,'width':1496,'height':967}}
        self.assertEqual(visible_regions([dock,target], target), [(981,263,400,400)])
        # Higher layers are naturally composed over our level-4 subtitle panel.
        # Windows between video and subtitle levels still require clipping.
        dock['layer'] = 3
        self.assertEqual(visible_regions([dock,target], target), [])
        dock['layer'] = -1
        self.assertEqual(visible_regions([dock,target], target), [(981,263,400,400)])

    def test_monitor_tracks_geometry_and_hides_on_stale_source_minimize_or_pid_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            target = host()
            source = {'mode':'serato', 'stale':False, 'effectiveSource':'A', 'selectedSource':'A',
                      'deckA':{'cacheState':'ready'}, 'readerDiagnostics':{'producers':PRODUCERS}}
            window = SimpleNamespace(visible=False, rectangle=(0,0,1,1))
            window.isVisible = lambda: window.visible
            window.show = lambda: setattr(window, 'visible', True)
            window.hide = lambda: setattr(window, 'visible', False)
            window.geometry = lambda: SimpleNamespace(getRect=lambda:window.rectangle)
            window.setGeometry = lambda *rect: setattr(window, 'rectangle', rect)
            window.setMask = lambda _: None
            bridge = SimpleNamespace(rows=snapshot(target), current=True, raised=0)
            bridge.snapshot = lambda: bridge.rows
            bridge.producer_is_current = lambda *_: bridge.current
            bridge.raise_window = lambda *_: setattr(bridge,'raised',bridge.raised+1)
            harness = SimpleNamespace(controller=SimpleNamespace(state=source), window=window,
                                      bridge=bridge, status_path=Path(folder)/'status.json')
            harness.status = MethodType(SeratoOverlay.status, harness)
            SeratoOverlay.tick(harness)
            self.assertTrue(window.visible)
            self.assertEqual(window.rectangle,(100,100,400,300))
            target['bounds'].update(x=-800,width=800)
            SeratoOverlay.tick(harness)
            self.assertEqual(window.rectangle,(-800,100,800,300))
            for field,value in (('stale',True),('selectedSource','none'),('mode','replay')):
                previous=source[field]
                source[field]=value
                SeratoOverlay.tick(harness)
                self.assertFalse(window.visible)
                source[field]=previous
            bridge.current=False
            SeratoOverlay.tick(harness)
            self.assertFalse(window.visible)
            bridge.current=True
            target['onScreen']=False
            SeratoOverlay.tick(harness)
            self.assertFalse(window.visible)


if __name__ == '__main__':
    unittest.main()
