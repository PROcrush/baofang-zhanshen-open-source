import copy
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
if sys.platform == 'darwin':
    from scripts.repair_qt_metadata import repair_qt_metadata
    repair_qt_metadata()

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

from worker.composition_render import (CompositionSampler, apply_transition,
                                       check_source_identity, validate_composition)
from worker.style_catalog import (DEFAULT_SETTINGS, EFFECTS, LAYOUTS, TEMPLATES,
                                  TEXT_LAYOUTS, TRANSITIONS, normalize_settings)
from worker.studio_render import (ENGINE_ID, _source_snapshot, draw_frame,
                                   find_ffmpeg, probe_media, render)

APPLICATION = QGuiApplication.instance() or QGuiApplication([])
SETTINGS = dict(DEFAULT_SETTINGS, width=320, height=180, fontSize=48)
SEGMENTS = [{'startMs': 0, 'endMs': 1200, 'text': '包房战神原创字幕'},
            {'startMs': 1300, 'endMs': 2200, 'text': '下一句也清晰'}]


def pixels(image):
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return rgba.constBits().tobytes()


def digest(image):
    return hashlib.sha256(pixels(image)).hexdigest()


def run_ffmpeg(arguments, **options):
    result = subprocess.run([find_ffmpeg(), '-hide_banner', '-loglevel', 'error'] + arguments,
                            capture_output=True, timeout=60, **options)
    if result.returncode:
        raise AssertionError(result.stderr.decode(errors='replace'))
    return result


def synthesize_wav(path, frequency, duration=2.5, amplitude=.04):
    rate = 48000
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(rate)
        stream.writeframes(b''.join(struct.pack('<h', round(amplitude * 32767 * math.sin(2 * math.pi * frequency * index / rate)))
                                  for index in range(round(duration * rate))))


def source_record(path, kind):
    if kind == 'image':
        image = QImage(str(path))
        duration, width, height = 0, image.width(), image.height()
    else:
        info = probe_media(path, include_video=kind == 'video')
        duration = float(info['format']['duration']) * 1000
        video = next((row for row in info['streams'] if row['codec_type'] == 'video'), {})
        width, height = video.get('width', 0), video.get('height', 0)
    return {'identity': _source_snapshot(path), 'kind': kind, 'durationMs': duration, 'width': width, 'height': height}


def build_fixture(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    main, extra, video, picture = [folder / name for name in ('main-220.wav', 'extra-880.wav', 'color-sequence.mp4', 'own-background.png')]
    synthesize_wav(main, 220)
    synthesize_wav(extra, 880, amplitude=.08)
    run_ffmpeg(['-n', '-f', 'lavfi', '-i', 'color=c=red:s=160x90:r=24:d=0.5',
                '-f', 'lavfi', '-i', 'color=c=green:s=160x90:r=24:d=0.5',
                '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=24:d=1.5',
                '-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]', '-map', '[v]',
                '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)])
    image = QImage(200, 100, QImage.Format.Format_RGBA8888)
    image.fill(QColor('#b347d0'))
    painter = QPainter(image); painter.fillRect(0, 0, 40, 100, QColor('#ffee22')); painter.end()
    image.save(str(picture))
    sources = {'video': source_record(video, 'video'), 'image': source_record(picture, 'image'),
               'extra': source_record(extra, 'audio')}
    composition = {'version': 1, 'sources': sources, 'clips': [
        {'id': 'clip-one', 'sourceId': 'video', 'kind': 'video', 'startMs': 0, 'durationMs': 2500 / 3,
         'sourceInMs': 0, 'rate': .5, 'fit': 'cover', 'locked': False, 'color': '#000000', 'transition': {'id': 'cut', 'durationMs': 0}},
        {'id': 'clip-two', 'sourceId': 'video', 'kind': 'video', 'startMs': 2500 / 3, 'durationMs': 2500 / 3,
         'sourceInMs': 1000, 'rate': 1.5, 'fit': 'contain', 'locked': False, 'color': '#000000', 'transition': {'id': 'wipe-left', 'durationMs': 625 / 3}},
        {'id': 'clip-three', 'sourceId': 'image', 'kind': 'image', 'startMs': 5000 / 3, 'durationMs': 2500 / 3,
         'sourceInMs': 0, 'rate': 1, 'fit': 'cover', 'locked': False, 'color': '#000000', 'transition': {'id': 'iris-up', 'durationMs': 625 / 3}},
    ], 'texts': [{'id': 'title', 'startMs': 150, 'durationMs': 2000, 'text': '独立制作 · 多分镜',
                  'x': .5, 'y': .16, 'fontSize': 30, 'fontFamily': 'PingFang SC', 'fontBold': True,
                  'color': '#ffee22', 'opacity': .95, 'layoutId': 'standard', 'animationId': 'rise', 'locked': False}],
        'audioClips': [{'id': 'mix', 'sourceId': 'extra', 'startMs': 400, 'durationMs': 1500,
                        'sourceInMs': 200, 'rate': .5, 'gain': .7, 'locked': False}],
        'markers': [], 'concert': {}}
    return main, composition


class CatalogDrawingTests(unittest.TestCase):
    def test_catalog_counts_unique_ids_and_parameter_presets(self):
        for catalog, count in ((TEMPLATES, 16), (EFFECTS, 8), (LAYOUTS, 184), (TRANSITIONS, 53)):
            self.assertEqual(len(catalog), count)
            self.assertEqual(len({row['id'] for row in catalog}), count)
            self.assertTrue(all(row.get('label') and row.get('description') for row in catalog))
        self.assertEqual(len({json.dumps(row['parameters'], sort_keys=True) for row in LAYOUTS}), 184)

    def test_every_template_paints_real_distinct_glyph_layout(self):
        hashes = set()
        for template in TEMPLATES:
            with self.subTest(template=template['id']):
                settings = dict(SETTINGS, templateId=template['id'])
                frame = draw_frame(SEGMENTS, settings, 420)
                self.assertNotEqual(digest(frame), digest(draw_frame([], settings, 420)))
                hashes.add(digest(frame))
        self.assertEqual(len(hashes), 16)

    def test_every_effect_draws_and_seven_animate_without_inventing_character_times(self):
        rows = copy.deepcopy(SEGMENTS)
        for effect in EFFECTS:
            with self.subTest(effect=effect['id']):
                settings = dict(SETTINGS, effectId=effect['id'])
                hashes = {digest(draw_frame(rows, settings, clock, transparent=True)) for clock in (40, 230, 570)}
                self.assertEqual(len(hashes), 1) if effect['id'] == 'none' else self.assertGreater(len(hashes), 1)
                self.assertNotEqual(digest(draw_frame(rows, settings, 230)), digest(draw_frame([], settings, 230)))
        self.assertEqual(rows, SEGMENTS)
        self.assertEqual(digest(draw_frame(rows, dict(SETTINGS, effectId='fade', fadeMs=0), 0, transparent=True)),
                         digest(draw_frame(rows, SETTINGS, 0, transparent=True)))

    def test_every_parameter_layout_draws_and_motion_variants_change_at_multiple_times(self):
        blank = digest(draw_frame([], SETTINGS, 0, transparent=True))
        for layout in LAYOUTS:
            with self.subTest(layout=layout['id']):
                settings = dict(SETTINGS, layoutId=layout['id'])
                hashes = {digest(draw_frame(SEGMENTS, settings, clock, transparent=True)) for clock in (80, 410, 870)}
                self.assertNotIn(blank, hashes)
                self.assertEqual(len(hashes), 1) if layout['id'] == 'standard' else self.assertGreater(len(hashes), 1)

    def test_every_transition_has_real_algorithm_direction_and_correct_endpoints(self):
        old = QImage(160, 90, QImage.Format.Format_RGBA8888); old.fill(QColor('#e82c16'))
        new = QImage(160, 90, QImage.Format.Format_RGBA8888); new.fill(QColor('#126ce8'))
        family_hashes = {}
        for transition in TRANSITIONS:
            with self.subTest(transition=transition['id']):
                identifier = transition['id']
                self.assertEqual(digest(apply_transition(old, new, identifier, 1)), digest(new))
                if identifier == 'cut':
                    self.assertEqual(digest(apply_transition(old, new, identifier, 0)), digest(new))
                    continue
                self.assertEqual(digest(apply_transition(old, new, identifier, 0)), digest(old))
                middle = digest(apply_transition(old, new, identifier, .37))
                self.assertNotIn(middle, {digest(old), digest(new)})
                self.assertNotEqual(middle, digest(apply_transition(old, new, identifier, .72)))
                family_hashes.setdefault(transition['algorithm'], set()).add(middle)
        self.assertTrue(all(len(hashes) == 4 for hashes in family_hashes.values()), family_hashes)

    def test_independent_text_all_catalog_layouts_and_transparent_rgba_layer(self):
        for layout in TEXT_LAYOUTS + LAYOUTS:
            with self.subTest(layout=layout['id']):
                composition = {'texts': [{'id': 'text', 'startMs': 0, 'durationMs': 1200, 'text': '独立文字',
                                          'fontSize': 38, 'layoutId': layout['id'], 'animationId': 'none'}]}
                frame = draw_frame([], SETTINGS, 400, composition=composition, transparent=True)
                alpha = pixels(frame)[3::4]
                self.assertEqual(min(alpha), 0)
                self.assertGreater(max(alpha), 200)
                self.assertNotEqual(digest(frame), digest(draw_frame([], SETTINGS, 400, transparent=True)))

    def test_invalid_settings_and_unknown_catalog_ids_are_rejected(self):
        for key, value in [('layoutId', 'unknown'), ('effectId', 'unknown'), ('templateId', 'unknown'),
                           ('entranceId', 'unknown'), ('strokeWidth', 13), ('x', float('nan')),
                           ('fps', 0), ('textColor', 'red'), ('letterSpacing', -1)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                normalize_settings(dict(SETTINGS, **{key: value}))

    def test_layout_parameter_overrides_repaint_and_reject_non_motion_fields(self):
        normal = draw_frame(SEGMENTS, SETTINGS, 400, transparent=True)
        changed = draw_frame(SEGMENTS, dict(SETTINGS, layoutParameters={'offsetX': .15, 'rotation': 20, 'scale': 1.2}),
                             400, transparent=True)
        self.assertNotEqual(digest(normal), digest(changed))
        for overrides in ({'motion': 'fake'}, {'scale': float('nan')}, {'amplitude': .6}, {'rotation': 181}):
            with self.assertRaises(ValueError):
                normalize_settings(dict(SETTINGS, layoutParameters=overrides))

    def test_karaoke_sweeps_word_or_sentence_timing_and_honors_switch(self):
        rows = [{'startMs': 0, 'endMs': 1000, 'text': '歌词', 'characters': [
            {'text': '歌', 'startMs': 0, 'endMs': 500}, {'text': '词', 'startMs': 500, 'endMs': 1000}]}]
        settings = dict(SETTINGS, templateId='karaoke')
        self.assertNotEqual(digest(draw_frame(rows, settings, 150, transparent=True)),
                            digest(draw_frame(rows, settings, 750, transparent=True)))
        sentence = [{'startMs': 0, 'endMs': 1000, 'text': '歌词'}]
        self.assertNotEqual(digest(draw_frame(sentence, settings, 150, transparent=True)),
                            digest(draw_frame(sentence, settings, 750, transparent=True)))
        settings['lyricSweep']=False
        for source in (rows,sentence):
            self.assertEqual(digest(draw_frame(source, settings, 150, transparent=True)),
                             digest(draw_frame(source, settings, 750, transparent=True)))


@unittest.skipUnless(find_ffmpeg(), 'A general FFmpeg executable is required')
class CompositionMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.folder.name)
        cls.audio, cls.composition = build_fixture(cls.directory)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_real_source_in_rate_and_fit_are_sampled_including_silent_video(self):
        with CompositionSampler(self.composition, 320, 180) as sampler:
            first = sampler.frame(600).pixelColor(120, 70)
            second = sampler.frame(1300).pixelColor(120, 70)
            third = sampler.frame(2200).pixelColor(120, 70)
            self.assertGreater(first.red(), 200)
            self.assertGreater(second.blue(), 200)
            self.assertGreater(third.red(), 100); self.assertGreater(third.blue(), 120)
            self.assertGreater(sampler.frame(910).pixelColor(5, 70).blue(), 150)
            self.assertGreater(sampler.frame(910).pixelColor(300, 70).red(), 150)

    def test_temporal_rate_and_crop_map_to_observed_source_colors(self):
        composition = copy.deepcopy(self.composition)
        composition['clips'] = [dict(composition['clips'][0], durationMs=1000, sourceInMs=300, rate=1.5)]
        with CompositionSampler(composition, 320, 180) as sampler:
            # source 300 ms is red; source 750 ms is green; source 1350 ms is blue.
            self.assertGreater(sampler.frame(0).pixelColor(10, 10).red(), 200)
            self.assertGreater(sampler.frame(300).pixelColor(10, 10).green(), 90)
            self.assertGreater(sampler.frame(700).pixelColor(10, 10).blue(), 200)

    def test_image_contain_and_cover_have_actual_different_crop(self):
        composition = copy.deepcopy(self.composition)
        clip = dict(composition['clips'][2], startMs=0, durationMs=500, transition={'id': 'cut', 'durationMs': 0}, fit='contain')
        composition['clips'] = [clip]
        with CompositionSampler(composition, 180, 180) as sampler:
            contained = sampler.frame(200)
            self.assertLess(contained.pixelColor(90, 5).red(), 5)
        clip['fit'] = 'cover'
        with CompositionSampler(composition, 180, 180) as sampler:
            covered = sampler.frame(200)
            self.assertGreater(covered.pixelColor(90, 5).red(), 100)
            self.assertNotEqual(digest(contained), digest(covered))

    def test_random_seek_cache_is_bounded_and_close_reaps_all_decoders(self):
        sampler = CompositionSampler(self.composition, 160, 90)
        for clock in (600, 0, 750, 350, 900, 1400, 1200, 1000, 50, 250, 500, 700, 1100, 1300, 1500, 800):
            sampler.frame(clock)
        self.assertLessEqual(len(sampler.cache), sampler.FRAME_CACHE_LIMIT)
        self.assertLessEqual(len(sampler.readers), sampler.DECODER_LIMIT)
        processes = [reader.process for reader in sampler.readers.values() if reader.process]
        sampler.close()
        self.assertTrue(all(process.poll() is not None for process in processes))
        with self.assertRaises(ValueError):
            sampler.frame(100)

    def test_invalid_source_windows_transitions_and_overlaps_rejected_before_decoding(self):
        modifications = [lambda c: c['clips'][0].update(sourceInMs=2400),
                         lambda c: c['clips'][0].update(rate=4.1),
                         lambda c: c['clips'][1].update(startMs=700),
                         lambda c: c['clips'][1].update(transition={'id': 'wipe-left', 'durationMs': 401}),
                         lambda c: c['clips'][2].update(transition={'id': 'unknown', 'durationMs': 625 / 3}),
                         lambda c: c['audioClips'][0].update(sourceInMs=2300),
                         lambda c: c['texts'][0].update(opacity=1.1)]
        for modify in modifications:
            bad = copy.deepcopy(self.composition); modify(bad)
            with self.assertRaises(ValueError):
                validate_composition(bad, 2500)

    def test_forged_media_duration_is_not_trusted_and_source_identity_changes_fail(self):
        bad = copy.deepcopy(self.composition)
        bad['sources']['video']['durationMs'] = 9000
        with self.assertRaises(ValueError):
            CompositionSampler(bad, 160, 90)
        source = self.directory / 'changed-source.png'
        source.write_bytes((self.directory / 'own-background.png').read_bytes())
        identity = _source_snapshot(source)
        source.write_bytes(source.read_bytes() + b'changed')
        with self.assertRaises(ValueError):
            check_source_identity(identity)

    def request(self, output, output_format='mp4'):
        return {'engineId': ENGINE_ID, 'audioPath': str(self.audio), 'durationMs': 2500,
                'segments': SEGMENTS, 'settings': dict(SETTINGS, templateId='karaoke'),
                'composition': copy.deepcopy(self.composition), 'output': str(output), 'outputFormat': output_format}

    def test_complete_three_shot_transition_text_and_audio_mix_export(self):
        output = self.directory / 'mixed-movie.mp4'
        source_hashes = {path: _source_snapshot(path) for path in [self.audio] + [source['identity']['path'] for source in self.composition['sources'].values()]}
        render(self.request(output))
        info = probe_media(output, verify_video=True)
        self.assertEqual({stream['codec_type'] for stream in info['streams']}, {'video', 'audio'})
        self.assertAlmostEqual(float(info['format']['duration']), 2.5, delta=.1)
        for clock, expected in [(.6, 'red'), (1.3, 'blue')]:
            frame = run_ffmpeg(['-ss', str(clock), '-i', str(output), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']).stdout
            offset = (40 * 320 + 15) * 3
            self.assertGreater(frame[offset if expected == 'red' else offset + 2], 150)
        samples = run_ffmpeg(['-i', str(output), '-map', '0:a:0', '-ac', '1', '-ar', '48000', '-f', 'f32le', '-']).stdout
        values = struct.unpack('<' + 'f' * (len(samples) // 4), samples)
        # The .5 playback rate preserves pitch via atempo; 880 Hz actually enters the mix.
        def energy(frequency, start, length):
            subset = values[round(start * 48000):round((start + length) * 48000)]
            real = sum(value * math.cos(2 * math.pi * frequency * i / 48000) for i, value in enumerate(subset))
            imag = sum(value * math.sin(2 * math.pi * frequency * i / 48000) for i, value in enumerate(subset))
            return math.hypot(real, imag) / len(subset)
        self.assertGreater(energy(880, .8, .3), energy(880, .05, .2) * 10 + .005)
        self.assertGreater(energy(220, .8, .3), .005)
        for path, identity in source_hashes.items():
            self.assertEqual(_source_snapshot(path), identity)

    def test_prores4444_alpha_is_real_transparent_and_text_survives_decoding(self):
        output = self.directory / 'subtitle-alpha.mov'
        request = self.request(output, 'mov-alpha')
        render(request)
        info = probe_media(output, include_video=True)
        self.assertEqual(len(info['streams']), 1)
        self.assertEqual(info['streams'][0]['codec_name'], 'prores')
        self.assertIn('a', info['streams'][0]['pix_fmt'])
        decoded = run_ffmpeg(['-ss', '0.6', '-i', str(output), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-']).stdout
        alpha = decoded[3::4]
        self.assertEqual(min(alpha), 0); self.assertGreater(max(alpha), 230)
        self.assertGreater(alpha.count(0), 320 * 180 * .6)
        self.assertTrue((self.directory / 'preview-frame.png').is_file())
        with self.assertRaises(ValueError):
            probe_media(output, verify_video=True)

    def test_export_refuses_to_overwrite_any_source_or_existing_output(self):
        for output in (self.audio, self.directory / 'own-background.png'):
            with self.assertRaises(ValueError):
                render(self.request(output))


if __name__ == '__main__':
    unittest.main()
