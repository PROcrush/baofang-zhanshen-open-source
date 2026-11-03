import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from app.studio_tools import (
    TEXT_LAYOUTS, build_delogo_command, correct_reference, export_subtitles,
    index_media_directory, load_subtitles, make_text_clip, map_concert_segments,
    merge_segments, remove_subtitles, split_segment, suggest_concert_pairing,
    suggest_duplicate_files, validate_segments, validate_text_clips,
)


def rows(texts, length=2):
    return [{'start': index * length, 'end': (index + 1) * length, 'text': text}
            for index, text in enumerate(texts)]


def timed(text='往事随疯'):
    return [{'start': 1, 'end': 3, 'text': text,
             'characters': [{'text': text[:2], 'start': 1, 'end': 1.8},
                            {'text': text[2:], 'start': 2.2, 'end': 3}]}]


class SubtitleToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def load(self, name, content, duration=None):
        path = self.root / name
        path.write_text(content, encoding='utf-8')
        return load_subtitles(path, duration)['segments']

    def test_srt_multiline_absolute_times_and_sentence_quality(self):
        content = '\ufeff1\r\n00:00:01,000 --> 00:00:03,000\r\n第一行\r\n第二行\r\n'
        imported = self.load('歌词.srt', content, 5)
        self.assertEqual(imported[0]['text'], '第一行\n第二行')
        self.assertEqual((imported[0]['start'], imported[0]['end']), (1, 3))
        self.assertNotIn('characters', imported[0])

    def test_json_token_gaps_preserved_and_does_not_modify_source(self):
        source = timed()
        path = self.root / '歌词.json'
        body = json.dumps({'segments': source}, ensure_ascii=False)
        path.write_text(body)
        self.assertEqual(load_subtitles(path, 3)['segments'], source)
        self.assertEqual(path.read_text(), body)

    def test_json_millisecond_rows_are_explicitly_adapted(self):
        imported = self.load('milliseconds.json', json.dumps({'segments': [
            {'startMs': 1000, 'endMs': 2000, 'text': '这是毫秒'}]}), 2)
        self.assertEqual((imported[0]['start'], imported[0]['end']), (1, 2))
        self.assertNotIn('startMs', imported[0])

    def test_our_existing_json_word_protocol_keeps_observed_intervals(self):
        imported = self.load('word-protocol.json', json.dumps({'segments': [
            {'startMs': 1000, 'endMs': 3000, 'text': '实际时码', 'words': [
                {'text': '实际', 'startMs': 1000, 'endMs': 1800},
                {'text': '时码', 'startMs': 2200, 'endMs': 3000}]}]}), 3)
        self.assertEqual(imported[0]['characters'], [
            {'text': '实际', 'start': 1, 'end': 1.8}, {'text': '时码', 'start': 2.2, 'end': 3}])

    def test_lrc_offset_multiple_starts_and_end_markers(self):
        imported = self.load('歌词.lrc', '[ar:测试歌手]\n[offset:100]\n'
                             '[00:01.000][00:04.000]同一段歌词\n[00:02.000]\n[00:05.000]\n', 6)
        self.assertEqual([(row['start'], row['end']) for row in imported], [(1.1, 2.1), (4.1, 5.1)])
        self.assertTrue(all('characters' not in row for row in imported))

    def test_enhanced_lrc_keeps_provided_token_times(self):
        imported = self.load('增强.lrc', '[00:01.00]<00:01.00>第一<00:02.00>句\n[00:03.00]\n', 4)
        self.assertEqual(imported[0]['text'], '第一句')
        self.assertEqual(imported[0]['characters'], [
            {'text': '第一', 'start': 1, 'end': 2}, {'text': '句', 'start': 2, 'end': 3}])

    def test_unknown_final_lrc_duration_is_explicitly_sentence_level(self):
        path = self.root / 'last.lrc'
        path.write_text('[00:10.00]最后一句\n')
        result = load_subtitles(path)
        self.assertEqual(result['segments'][0]['end'], 15)
        self.assertIn('5 秒', result['notice'])
        self.assertFalse(result['acousticAccuracyVerified'])

    def test_jianying_only_subtitle_track_and_microsecond_clock(self):
        draft = {'materials': {'texts': [
            {'id': 'caption', 'type': 'subtitle', 'content': json.dumps({'text': '字幕'})},
            {'id': 'title', 'type': 'text', 'content': json.dumps({'text': '普通标题'})}]},
            'tracks': [{'type': 'text', 'segments': [
                {'material_id': 'caption', 'target_timerange': {'start': 1000000, 'duration': 2000000}},
                {'material_id': 'title', 'target_timerange': {'start': 0, 'duration': 5000000}}]}]}
        imported = self.load('draft.json', json.dumps(draft), 5)
        self.assertEqual(imported, [{'start': 1, 'end': 3, 'text': '字幕', 'timingQuality': 'sentence'}])

    def test_malformed_or_partly_invalid_input_never_silently_imported(self):
        samples = [('bad.srt', '1\n00:00:01,000 --> 00:00:02,000\n有效\n\n坏块\n'),
                   ('bad.lrc', '[00:01.00]有效\n没有时间戳\n'),
                   ('duplicate.lrc', '[00:01.00]第一句\n[00:01.00]第二句\n'),
                   ('nan.json', '{"segments":[{"start":NaN,"end":2,"text":"坏"}]}'),
                   ('duplicate.json', '{"segments":[],"segments":[]}'),
                   ('structure.json', '{"tracks":[],"materials":null}')]
        for name, content in samples:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.load(name, content)

    def test_nonfinite_boolean_overlap_and_token_mismatch_refused(self):
        cases = [[{'start': start, 'end': 3, 'text': '歌词'}] for start in (True, -1, float('nan'), 3)]
        cases += [[{'start': 0, 'end': 2, 'text': '第一句'}, {'start': 1, 'end': 3, 'text': '重叠'}]]
        broken = timed()
        broken[0]['characters'][1]['text'] = '错误文字'
        cases.append(broken)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_segments(value)
        with self.assertRaises(ValueError):
            validate_segments(timed(), duration=2)

    def test_json_and_srt_roundtrip_and_lrc_preserves_silent_gap(self):
        source = [{'start': 1, 'end': 2, 'text': '第一句'}, {'start': 4, 'end': 5, 'text': '第二句'}]
        for format in ('srt', 'json', 'lrc'):
            content = export_subtitles(source, format)
            imported = self.load('roundtrip.' + format, content, 6)
            self.assertEqual([(row['start'], row['end'], row['text']) for row in imported],
                             [(row['start'], row['end'], row['text']) for row in source])

    def test_new_file_export_cannot_overwrite_or_follow_output_symlink(self):
        target = self.root / 'lyrics.srt'
        export_subtitles(rows(['保存歌词']), 'srt', target)
        before = target.read_bytes()
        with self.assertRaises(FileExistsError):
            export_subtitles(rows(['其他歌词']), 'srt', target)
        link = self.root / 'alias.srt'
        link.symlink_to(target)
        with self.assertRaises(FileExistsError):
            export_subtitles(rows(['其他歌词']), 'srt', link)
        self.assertEqual(target.read_bytes(), before)

    def test_submillisecond_srt_interval_cannot_disappear(self):
        with self.assertRaisesRegex(ValueError, '舍入'):
            export_subtitles([{'start': 1.0001, 'end': 1.0004, 'text': '太短'}], 'srt')

    def test_reference_typo_retains_supplied_token_pauses(self):
        source = timed()
        before = copy.deepcopy(source)
        result = correct_reference(source, '往事随风')
        self.assertEqual(result['segments'][0]['text'], '往事随风')
        self.assertEqual([token['text'] for token in result['segments'][0]['characters']], ['往事', '随风'])
        self.assertEqual([(token['start'], token['end']) for token in result['segments'][0]['characters']],
                         [(1, 1.8), (2.2, 3)])
        self.assertFalse(result['timingRealigned'])
        self.assertEqual(source, before)

    def test_reference_length_change_has_no_fabricated_tokens(self):
        source = timed('今晚跟着节奏起')
        result = correct_reference(source, '今晚跟着节奏起舞')
        self.assertEqual(result['segments'][0]['text'], '今晚跟着节奏起舞')
        self.assertNotIn('characters', result['segments'][0])
        self.assertEqual((result['segments'][0]['start'], result['segments'][0]['end']), (1, 3))

    def test_ambiguous_reference_preserves_original_and_review(self):
        result = correct_reference(timed(), '往事随风\n往事随风')
        self.assertEqual(result['segments'][0]['text'], '往事随疯')
        self.assertEqual(result['changes'], [])
        self.assertEqual(result['review'][0]['reasonCode'], 'ambiguous_reference')

    def test_split_merge_exact_boundary_preserves_actual_token_clock(self):
        source = timed()
        before = copy.deepcopy(source)
        split = split_segment(source, 0, 2, text_index=2)
        self.assertEqual([row['text'] for row in split], ['往事', '随疯'])
        self.assertEqual(split[0]['characters'][0]['end'], 1.8)
        self.assertEqual(split[1]['characters'][0]['start'], 2.2)
        merged = merge_segments(split, [0, 1])
        self.assertEqual(merged[0]['characters'], source[0]['characters'])
        self.assertEqual(source, before)

    def test_split_inside_token_downgrades_and_invalid_edit_refused(self):
        split = split_segment(timed(), 0, 1.5, text_index=1)
        self.assertTrue(all('characters' not in row for row in split))
        with self.assertRaises(ValueError):
            split_segment(timed(), 0, 1)
        with self.assertRaises(ValueError):
            merge_segments(rows(['第一行', '第二行', '第三行']), [0, 2])


class LayoutAndConcertTests(unittest.TestCase):
    def test_original_layouts_valid_and_bad_style_rejected(self):
        self.assertEqual(len(TEXT_LAYOUTS), 5)
        for layout in TEXT_LAYOUTS:
            clip = make_text_clip('独立标题', 0, 3, layout=layout['id'])
            self.assertEqual(clip['layout'], layout['id'])
        for style in ({'opacity': 1.1}, {'fontSize': 0}, {'color': None}, {'fontBold': 'yes'}):
            with self.subTest(style=style), self.assertRaises(ValueError):
                make_text_clip('独立标题', 0, 3, **style)

    def test_text_concurrency_uses_end_exclusive_boundary(self):
        clips = [make_text_clip('标题', 0, 1, id=str(index)) for index in range(12)]
        validate_text_clips(clips + [make_text_clip('下一段', 1, 1, id='next')])
        with self.assertRaisesRegex(ValueError, '12'):
            validate_text_clips(clips + [make_text_clip('第13层', .5, 1, id='overflow')])

    def test_line_character_and_duration_limits(self):
        for text in ('字' * 121, '\n'.join('行' for _ in range(9))):
            with self.assertRaises(ValueError):
                make_text_clip(text, 0, 1)
        with self.assertRaises(ValueError):
            validate_text_clips([make_text_clip('超时', 1, 2)], duration=2)

    def test_dp_skips_unrelated_lyrics_and_retains_monotonic_pairing(self):
        source = rows(['舞台介绍', '往事随风', '今晚一起舞动'])
        target = rows(['往事随风', '没有现场版本', '今晚一起舞动'])
        before = copy.deepcopy(source)
        pairing = suggest_concert_pairing(source, target)
        pairs = [row for row in pairing['rows'] if row['action'] == 'pair']
        self.assertEqual([(row['sourceIndices'], row['targetIndices']) for row in pairs], [([1], [0]), ([2], [2])])
        self.assertEqual(pairing['summary']['unmatchedTargetLines'], 1)
        self.assertTrue(pairing['candidateOnly'])
        self.assertFalse(pairing['visualSafetyVerified'])
        self.assertEqual(source, before)

    def test_dp_supports_split_target_line_group(self):
        source = rows(['往事随风飘散'])
        target = rows(['往事随风', '飘散'], length=1)
        pairing = suggest_concert_pairing(source, target)
        pairs = [row for row in pairing['rows'] if row['action'] == 'pair']
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]['targetIndices'], [0, 1])
        self.assertEqual(pairs[0]['similarity'], 1)

    def test_repeated_chorus_candidates_flag_ambiguity(self):
        pairing = suggest_concert_pairing(rows(['往事随风', '往事随风']), rows(['往事随风']))
        pair = next(row for row in pairing['rows'] if row['action'] == 'pair')
        self.assertTrue(pair['reviewRequired'])
        self.assertEqual(pair['candidateMargin'], 0)

    def test_mapping_retains_target_duration_and_refuses_unsafe_speed(self):
        pairing = suggest_concert_pairing(rows(['往事随风'], length=2), rows(['往事随风'], length=4))
        clip = map_concert_segments(pairing)[0]
        self.assertEqual((clip['timelineStart'], clip['timelineDuration'], clip['playbackRate']), (0, 4, .5))
        self.assertEqual(clip['kind'], 'lipSyncCandidate')
        self.assertTrue(clip['reviewRequired'])
        self.assertEqual(map_concert_segments(pairing, minimum_rate=.75)[0]['kind'], 'cutawayRequired')

    def test_bad_manual_map_and_unbounded_dp_refused(self):
        pairing = suggest_concert_pairing(rows(['同一句歌词']), rows(['同一句歌词']))
        pairing['rows'][0]['sourceIndices'] = [999]
        with self.assertRaises(ValueError):
            map_concert_segments(pairing)
        with self.assertRaises(ValueError):
            suggest_concert_pairing(rows(['字幕'] * 301), rows(['字幕'] * 301))


class FilesAndDelogoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.video = self.root / '原视频.mov'
        self.video.write_bytes(b'fixture source is never modified')
        self.ffmpeg = self.root / 'ffmpeg-stub'
        self.ffmpeg.write_text('#!/usr/bin/env python3\nimport pathlib,sys\npathlib.Path(sys.argv[-1]).write_bytes(b"staged output")\n')
        self.ffmpeg.chmod(0o700)

    def test_delogo_command_pixel_roi_preview_and_no_overwrite(self):
        output = self.root / 'preview.mp4'
        command = build_delogo_command(self.video, output, (10, 20, 100, 30), self.ffmpeg,
                                       preview_start=2, preview_duration=3, frame_size=(640, 360))
        self.assertIn('-n', command)
        self.assertNotIn('-y', command)
        self.assertEqual(command[command.index('-vf') + 1], 'delogo=x=10:y=20:w=100:h=30:show=0')
        self.assertEqual(command[command.index('-t') + 1], '3.0')
        self.assertIn('0:a?', command)
        output.write_bytes(b'previous result')
        with self.assertRaises(ValueError):
            build_delogo_command(self.video, output, (10, 20, 100, 30), self.ffmpeg)

    def test_invalid_delogo_roi_and_original_bundle_dependency_rejected(self):
        for roi in [(0, 20, 30, 40), (10, 20, 630, 30), (.1, .2, .3, .4), (True, 1, 5, 5)]:
            with self.subTest(roi=roi), self.assertRaises(ValueError):
                build_delogo_command(self.video, self.root / 'new.mp4', roi, self.ffmpeg, frame_size=(640, 360))
        prohibited = self.root / '歌暮.app' / 'Contents' / 'ffmpeg'
        prohibited.parent.mkdir(parents=True)
        prohibited.write_text('#!/bin/sh\nexit 0\n')
        prohibited.chmod(0o700)
        with self.assertRaisesRegex(ValueError, '原包'):
            build_delogo_command(self.video, self.root / 'new.mp4', (1, 1, 2, 2), prohibited)

    def test_delogo_private_staging_exclusive_publish_and_source_unchanged(self):
        output = self.root / 'new.mp4'
        before = self.video.read_bytes()
        result = remove_subtitles(self.video, output, (10, 20, 30, 40), self.ffmpeg)
        self.assertEqual(output.read_bytes(), b'staged output')
        self.assertEqual(self.video.read_bytes(), before)
        self.assertFalse(result['temporalRecovery'])
        self.assertFalse(list(self.root.glob('.baofang-delogo-*')))

    def test_failed_delogo_does_not_publish_partial_file(self):
        self.ffmpeg.write_text('#!/usr/bin/env python3\nimport pathlib,sys\n'
                               'pathlib.Path(sys.argv[-1]).write_bytes(b"partial")\nsys.exit(1)\n')
        output = self.root / 'failed.mp4'
        with self.assertRaises(RuntimeError):
            remove_subtitles(self.video, output, (10, 20, 30, 40), self.ffmpeg)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob('.baofang-delogo-*')))

    def test_index_selected_media_only_without_following_symlink(self):
        media = self.root / 'selected'
        media.mkdir()
        (media / 'song.wav').write_bytes(b'audio')
        (media / 'unrelated.json').write_text('{}')
        (media / 'linked.mov').symlink_to(self.video)
        nested = media / 'nested'
        nested.mkdir()
        (nested / 'image.png').write_bytes(b'image')
        indexed = index_media_directory(media)
        self.assertEqual({entry['relativePath'] for entry in indexed['files']}, {'song.wav', 'nested/image.png'})
        self.assertTrue(indexed['readOnly'])
        self.assertEqual(len(index_media_directory(media, recursive=False)['files']), 1)
        self.assertTrue(index_media_directory(media, max_files=1)['truncated'])

    def test_sha_duplicates_and_hard_links_only_produce_suggestions(self):
        first, second, third, different = [self.root / name for name in ('a.wav', 'b.wav', 'hard.wav', 'other.wav')]
        first.write_bytes(b'same audio')
        second.write_bytes(b'same audio')
        os.link(first, third)
        different.write_bytes(b'not audio!')
        result = suggest_duplicate_files([first, second, third, different])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['physicalFileCount'], 2)
        self.assertEqual(result[0]['action'], 'review_only')
        self.assertFalse(result[0]['deleted'])
        self.assertTrue(all(path.exists() for path in (first, second, third, different)))
        self.assertEqual(first.read_bytes(), b'same audio')


if __name__ == '__main__':
    unittest.main()
