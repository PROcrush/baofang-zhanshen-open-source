import copy
import unittest

from app.controller import initial_state
from app.preshow import report
from app.source_routing import select_lyric_source

SCREENS = [{'name': 'Built-in', 'label': '显示器 1 · Built-in · 1512×982'},
           {'name': 'TV', 'label': '显示器 2 · TV · 1920×1080'}]


def live_state(**changes):
    state = copy.deepcopy(initial_state())
    state.update(mode='serato', inputMode='serato', bridgeRunning=True, readerConnected=True, stale=False,
                 selectedSource='auto', effectiveSource='A', outputOpen=True, outputTarget={'screenName': 'TV', 'fullScreen': True},
                 bridgeStatus='已连接 Serato Video')
    state['deckA'] = dict(state['deckA'], title='夜色开场', cacheState='ready', needsReview=False, cacheLabel='歌词缓存已就绪')
    state['deckB'] = dict(state['deckB'], title='', cacheState='suspended', cacheLabel='暂无有效播放观测')
    state['offlineLibrary'] = dict(state['offlineLibrary'], offlineOnly=True)
    state.update(changes)
    return state


def rows(state, screens=SCREENS, lan_ip='192.168.1.20'):
    return {row['id']: row for row in report(state, screens, lan_ip)['rows']}


class PreshowTest(unittest.TestCase):
    def test_ready_show_is_all_quiet(self):
        result = report(live_state(), SCREENS, '192.168.1.20')
        self.assertEqual((result['broken'], result['todo']), (0, 0))
        self.assertEqual(result['summary'], '能查的都正常。')
        found = {row['id']: row for row in result['rows']}
        self.assertEqual(list(found), ['dj', 'output', 'lyrics', 'deckA', 'deckB', 'setlist', 'midi', 'wish', 'cloud'])
        self.assertEqual(found['dj']['tone'], 'green')
        self.assertIn('显示器 2 · TV', found['output']['detail'])
        self.assertIn('全屏', found['output']['detail'])
        self.assertEqual(found['deckA']['detail'], '《夜色开场》歌词就绪。')
        self.assertEqual(found['deckB']['tone'], 'muted')
        self.assertEqual(found['cloud']['tone'], 'green')

    def test_replay_is_flagged_and_decks_are_samples(self):
        found = rows(copy.deepcopy(initial_state()))
        self.assertEqual(found['dj']['tone'], 'amber')
        self.assertIn('回放演示', found['dj']['detail'])
        self.assertEqual(found['deckA']['tone'], 'muted')
        self.assertEqual(found['output']['action'], 'openOutput')

    def test_dj_not_connected_is_red_with_reason_and_connect(self):
        found = rows(live_state(readerConnected=False, stale=True, bridgeStatus='accessibility_permission_required'))
        self.assertEqual(found['dj']['tone'], 'red')
        self.assertIn('accessibility_permission_required', found['dj']['detail'])
        self.assertEqual(found['dj']['action'], 'connect')
        self.assertEqual(rows(live_state(bridgeRunning=False))['dj']['actionLabel'], '重新连接')

    def test_connected_but_nothing_playing_is_amber(self):
        found = rows(live_state(stale=True))
        self.assertEqual(found['dj']['tone'], 'amber')
        self.assertEqual(found['dj']['action'], '')

    def test_long_status_is_shortened(self):
        detail = rows(live_state(readerConnected=False, bridgeStatus='等' * 500))['dj']['detail']
        self.assertLess(len(detail), 160)

    def test_output_states(self):
        self.assertEqual(rows(live_state(outputOpen=False), screens=SCREENS[:1])['output']['detail'],
                         '还没打开。现在只接了一块屏，打开后会盖住控制台。')
        gone = rows(live_state(), screens=SCREENS[:1])['output']
        self.assertEqual((gone['tone'], gone['action']), ('red', 'screens'))
        single = rows(live_state(outputTarget={'screenName': '', 'fullScreen': False}), screens=SCREENS[:1])['output']
        self.assertEqual((single['tone'], single['action']), ('amber', 'screens'))
        unset = rows(live_state(outputTarget={'screenName': '', 'fullScreen': False}))['output']
        self.assertEqual(unset['detail'], '已打开，还没选显示器。')
        window = rows(live_state(outputTarget={'screenName': 'TV', 'fullScreen': False}))['output']
        self.assertIn('窗口显示', window['detail'])
        overlay = rows(live_state(outputDestination='serato_overlay', overlayStatus={'phase': 'searching', 'detail': '正在定位'}))['output']
        self.assertEqual(overlay['tone'], 'amber')
        attached = rows(live_state(outputDestination='serato_overlay', overlayStatus={'phase': 'attached', 'detail': '已叠加'}))['output']
        self.assertEqual(attached['tone'], 'green')

    def test_hidden_lyrics_offer_restore(self):
        found = rows(live_state(selectedSource='none'))['lyrics']
        self.assertEqual((found['tone'], found['action']), ('amber', 'restore'))
        self.assertIn('固定 B 盘', rows(live_state(selectedSource='B'))['lyrics']['detail'])

    def test_unavailable_auto_route_cannot_report_all_green(self):
        sources = {side: {'ready': True} for side in 'AB'}
        for mode in ('serato', 'rekordbox'):
            selection = select_lyric_source('auto', -1, 'A', sources, video_fader_ready=False)
            state = live_state(mode=mode, effectiveSource=selection['effectiveSource'], selection=selection)
            result = report(state, SCREENS, '192.168.1.20')
            found = {row['id']: row for row in result['rows']}['lyrics']
            self.assertEqual((found['tone'], found['action']), ('amber', 'routing'))
            self.assertIn('A / B', found['detail'])
            self.assertNotEqual(result['summary'], '能查的都正常。')

    def test_centered_auto_route_and_unready_manual_source_are_warnings(self):
        cases = [('auto', .5, {'A': {'ready': True}}),
                 ('B', -1, {'B': {'ready': False, 'reasonCode': 'lyrics_missing', 'reason': '选中盘尚未准备歌词'}})]
        for selected, fader, sources in cases:
            selection = select_lyric_source(selected, fader, None, sources)
            found = rows(live_state(selectedSource=selected, effectiveSource='none', selection=selection))['lyrics']
            self.assertEqual(found['tone'], 'amber')
            self.assertIn(selection['reason'], found['detail'])

    def test_ready_manual_source_and_lyric_gap_remain_green(self):
        selection = select_lyric_source('B', -1, None, {'B': {'ready': True}}, video_fader_ready=False)
        state = live_state(selectedSource='B', effectiveSource=selection['effectiveSource'], selection=selection,
                           lyric={'visible': False, 'text': ''})
        self.assertEqual(rows(state)['lyrics']['tone'], 'green')
        self.assertIn('固定 B 盘', rows(state)['lyrics']['detail'])

    def test_running_loopback_qr_still_warns_after_joining_wifi(self):
        for host in ('127.0.0.1', 'localhost', '[::1]'):
            state = live_state(guestQr={'running': True, 'url': f'http://{host}:5000/private-token', 'onScreen': True})
            found = rows(state, lan_ip='192.168.1.20')['wish']
            self.assertEqual((found['tone'], found['action']), ('amber', 'wish'))
            self.assertIn('关闭再打开', found['detail'])
            self.assertNotIn('private-token', found['detail'])

    def test_running_qr_without_lan_or_with_old_address_is_not_green(self):
        for url, lan in (('http://192.168.1.20:5000/x', '127.0.0.1'),
                         ('http://192.168.1.20:5000/x', '192.168.2.20'),
                         ('', '192.168.1.20'), ('http://[invalid', '192.168.1.20')):
            state = live_state(guestQr={'running': True, 'url': url})
            self.assertEqual(rows(state, lan_ip=lan)['wish']['tone'], 'amber')

    def test_output_recovery_shows_progress_or_retry_without_changing_destination(self):
        waiting = rows(live_state(outputOpen=False, outputRecovery={
            'phase': 'waiting', 'detail': '1 秒后重开（1/3）'}))['output']
        self.assertEqual((waiting['tone'], waiting['action']), ('amber', ''))
        self.assertIn('1/3', waiting['detail'])
        failed = rows(live_state(outputOpen=False, outputDestination='serato_overlay',
                                 outputRecovery={'phase': 'failed', 'detail': '自动重开失败'}))['output']
        self.assertEqual((failed['tone'], failed['action']), ('red', 'retryOutput'))

    def test_deck_cache_states(self):
        def deck(**values):
            state = live_state()
            state['deckA'] = dict(state['deckA'], **values)
            return rows(state)['deckA']
        self.assertEqual(deck(needsReview=True)['tone'], 'amber')
        missing = deck(cacheState='missing')
        self.assertEqual((missing['tone'], missing['action']), ('amber', 'prepare'))
        binding = deck(cacheState='preparing', cacheLabel='请确认此盘实际歌曲文件')
        self.assertEqual(binding['detail'], '《夜色开场》：请确认此盘实际歌曲文件')
        self.assertEqual(deck(cacheState='error', cacheLabel='文件核验失败')['tone'], 'red')
        self.assertEqual(deck(title='')['detail'], '这首歌歌词就绪。')

    def test_setlist_counts(self):
        empty = rows(live_state())['setlist']
        self.assertEqual((empty['tone'], empty['action']), ('muted', 'library'))
        ready = {'total': 3, 'counts': {'ready': 2, 'review': 1, 'missing': 0, 'gone': 0}}
        self.assertEqual(rows(live_state(setlist=ready))['setlist']['detail'], '3 首 · 就绪 2 · 待复核 1')
        self.assertEqual(rows(live_state(setlist=ready))['setlist']['tone'], 'green')
        short = {'total': 4, 'counts': {'ready': 1, 'review': 0, 'missing': 2, 'gone': 1}}
        found = rows(live_state(setlist=short))['setlist']
        self.assertEqual((found['tone'], found['action']), ('amber', 'library'))
        self.assertEqual(found['detail'], '4 首 · 就绪 1 · 没歌词 2 · 找不到 1')

    def test_midi_states(self):
        mapped = [{'action': 'toggleLyrics', 'key': 'note:0:40'}, {'action': 'tap', 'key': ''}]
        self.assertEqual(rows(live_state())['midi']['tone'], 'muted')
        self.assertEqual(rows(live_state(midi={'port': 'DDJ', 'connected': True, 'rows': mapped}))['midi']['tone'], 'green')
        lost = rows(live_state(midi={'port': 'DDJ', 'connected': False, 'rows': mapped}))['midi']
        self.assertEqual((lost['tone'], lost['action']), ('red', 'midi'))
        self.assertEqual(rows(live_state(midi={'port': '', 'connected': False, 'rows': mapped}))['midi']['tone'], 'amber')
        self.assertEqual(rows(live_state(midi={'port': 'DDJ', 'connected': True, 'rows': []}))['midi']['action'], 'midi')
        self.assertEqual(rows(live_state(midi={'available': False, 'port': 'DDJ', 'rows': mapped}))['midi']['tone'], 'red')

    def test_wish_states(self):
        on = rows(live_state(guestQr={'running': True, 'url': 'http://192.168.1.20:5000/x', 'onScreen': True}))['wish']
        self.assertEqual(on['tone'], 'green')
        self.assertIn('客屏角', on['detail'])
        self.assertNotIn('http', on['detail'])
        self.assertIn('没连局域网', rows(live_state(), lan_ip='127.0.0.1')['wish']['detail'])
        self.assertIn('192.168.1.20', rows(live_state())['wish']['detail'])

    def test_cloud_offers_offline_mode(self):
        state = live_state()
        state['offlineLibrary'] = dict(state['offlineLibrary'], offlineOnly=False)
        found = rows(state)['cloud']
        self.assertEqual((found['tone'], found['action']), ('muted', 'offline'))
        self.assertIn('没填百炼 Key', found['detail'])
        state['preparation'] = {'config': {'ready': True}}
        self.assertIn('百炼 Key 已填', rows(state)['cloud']['detail'])

    def test_summary_counts_red_and_amber(self):
        result = report(live_state(readerConnected=False, selectedSource='none'), SCREENS, '192.168.1.20')
        self.assertEqual((result['broken'], result['todo']), (1, 1))
        self.assertEqual(result['summary'], '1 项有问题，先处理红点。另有 1 项要看一下。')
        result = report(live_state(selectedSource='none'), SCREENS, '192.168.1.20')
        self.assertEqual(result['summary'], '1 项要看一下，其余正常。')

    def test_bad_input_does_not_crash(self):
        result = report({'setlist': {'total': 'x'}, 'midi': {'rows': [None, 'x']}, 'outputOpen': True}, [None, 'x'], None)
        self.assertEqual(len(result['rows']), 9)
        self.assertEqual(report(None)['rows'][0]['id'], 'dj')


if __name__ == '__main__':
    unittest.main()
