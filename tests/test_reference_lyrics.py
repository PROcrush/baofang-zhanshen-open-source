import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worker.ai_tasks import run
from worker.lyric_reference import guess_title, plain_lines, reference, search
from worker.reference_match import match, rank


class Transport:
    def __init__(self):
        self.calls=[]

    def json(self,host,path,body=None):
        self.calls.append((host,path,body))
        if 'search/get' in path:
            return {'code':200,'result':{'songs':[{'id':123,'name':'今晚的节奏','artists':[{'name':'测试歌手'}]}]}}
        return {'code':200,'lrc':{'lyric':'[01:29.000]今晚的节奏跟随心跳\n[02:35.000]窗外雨声伴着微笑'}}


def row(text,start=32000):
    words=[{'text':c,'startMs':start+i*100,'endMs':start+(i+1)*100} for i,c in enumerate(text)]
    return {'text':text,'startMs':start,'endMs':start+len(text)*100,'words':words}


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.ref=reference('netease',123,'今晚的节奏','测试歌手','今晚的节奏跟随心跳\n窗外雨声伴着微笑\n今晚的节奏跟随心跳')

    def test_title_and_lyric_clock_removal(self):
        self.assertEqual(guess_title('测试歌名(Black Remix)#.wav'),'测试歌名')
        self.assertEqual(plain_lines('[ti:歌词]\n[00:00.0]作词 : 作者\n[02:34.1][03:45.5]今晚的节奏'),['今晚的节奏'])

    def test_homophones_preserve_native_word_clock_and_original(self):
        rows=[row('今完的节奏跟随心跳，',start=38000)]
        before=copy.deepcopy(rows)
        p=match(rows,self.ref)
        self.assertEqual(p['rows'][0]['text'],'今晚的节奏跟随心跳，')
        self.assertEqual([(w['startMs'],w['endMs']) for w in p['rows'][0]['words']],[(w['startMs'],w['endMs']) for w in rows[0]['words']])
        self.assertEqual(p['rows'][0]['startMs'],38000)
        self.assertEqual(p['changes'][0]['originalWords'],rows[0]['words'])
        self.assertEqual(rows,before)
        self.assertFalse(p['originalClockUsed'])

    def test_merge_repeat_reorder_and_punctuation(self):
        rows=[row('窗外雨声伴着微笑，今完的节奏跟随心跳。'),row('今完的节奏跟随心跳。',48000)]
        p=match(rows,self.ref)
        self.assertEqual(len(p['changes']),2)
        self.assertEqual(p['rows'][0]['text'],'窗外雨声伴着微笑，今晚的节奏跟随心跳。')

    def test_truncated_vocal_and_dj_do_not_get_completed(self):
        rows=[row('今晚的节奏，'),row('DJ BLACK IN THE MIX',48000),row('雨声。',59000)]
        p=match(rows,self.ref)
        self.assertEqual(p['rows'],rows)
        self.assertEqual(p['changes'],[])
        self.assertEqual(len(p['unresolved']),3)

    def test_ambiguous_reference_phrase_does_not_replace(self):
        ref=reference('netease',123,'测试','测试','今晚的节奏跟随心跳\n今晚的节奏跟随新潮')
        p=match([row('今完的节奏跟随心潮')],ref)
        self.assertFalse(p['changes'])

    def test_reference_length_change_is_unselected_and_keeps_sentence_times(self):
        text='这个夜晚我们携手一起走向远方'
        ref=reference('netease',123,'测试','测试',text)
        original=row('这个夜晚我们手一起走向远方')
        p=match([original],ref)
        self.assertEqual(len(p['changes']),1)
        self.assertFalse(p['changes'][0]['selected'])
        self.assertNotIn('words',p['rows'][0])
        self.assertEqual(p['rows'][0]['endMs'],original['endMs'])
        self.assertEqual(p['changes'][0]['originalWords'],original['words'])

    def test_wrong_song_and_multiple_versions_require_selection(self):
        rows=[row('今完的节奏跟随心跳')]
        candidates,p=rank(rows,[self.ref],'别的歌')
        self.assertIsNone(p)
        other=reference('netease',124,'今晚的节奏','另一歌手','今晚的节奏跟随心跳\n不一样的完整歌词')
        _,p=rank(rows,[self.ref,other],'今晚的节奏')
        self.assertIsNone(p)
        _,p=rank(rows,[self.ref],'今晚的节奏','测试歌手')
        self.assertIsNotNone(p)

    def test_anonymous_search_cache_and_offline_integrity(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=str(Path(folder)/'new-cache')
            transport=Transport()
            result=search('今晚的节奏','',folder,transport=transport)
            self.assertFalse(result['cached'])
            self.assertEqual(len(transport.calls),2)
            with patch.object(Transport,'json',side_effect=AssertionError('offline must not use network')):
                cached=search('今晚的节奏','',folder,offline=True,transport=Transport())
            self.assertTrue(cached['cached'])
            path=next(Path(folder).glob('*.json'))
            value=json.loads(path.read_text());value['candidates'][0]['lines'][0]='缓存被修改'
            path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'没有可用'):
                search('今晚的节奏','',folder,offline=True)

    def test_failed_primary_falls_back_without_credentials(self):
        class Fallback:
            def json(self,host,path,body=None):
                if host=='music.163.com':raise OSError('unavailable')
                return [{'id':12,'trackName':'测试','artistName':'歌手','syncedLyrics':'[00:20.0]今晚的节奏跟随心跳'}]
        with tempfile.TemporaryDirectory() as folder:
            result=search('测试','',folder,transport=Fallback())
        self.assertEqual(result['candidates'][0]['provider'],'lrclib')
        self.assertTrue(result['notices'])

    def test_worker_reference_route_needs_no_model_config_or_key_and_checks_audio(self):
        from app.storage import inspect_audio
        import wave
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.wav'
            with wave.open(str(path),'wb') as stream:
                stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000);stream.writeframes(bytes(16000))
            request={'action':'search-reference','track':inspect_audio(path),'title':'今晚的节奏','artist':'',
                     'rows':[row('今完的节奏跟随心跳',0)],'cacheDirectory':folder,'offline':False}
            result=run(request,transport=Transport())
            self.assertEqual(result['phase'],'review')
            self.assertTrue(result['proposal']['changes'])
            path.write_bytes(path.read_bytes()+b'changed')
            with self.assertRaisesRegex(ValueError,'音频版本'):
                run(request,transport=Transport())


if __name__=='__main__':
    unittest.main()
