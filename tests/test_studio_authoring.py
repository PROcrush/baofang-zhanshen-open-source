import copy
import json
import subprocess
import tempfile
import unittest
import wave
from unittest.mock import patch
from pathlib import Path

from tests.test_studio import APPLICATION,ROOT,synthesize_audio,wait_idle
from PySide6.QtGui import QImage,QColor
from PySide6.QtCore import QSize
from app.studio import StudioController,file_identity
from app.studio_preview import StudioPreviewProvider
from worker.studio_render import find_ffmpeg,probe_media
from worker.studio_audio import analyze_audio
from worker.studio_video import inspect_video


class AuthoringIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.folder=Path(self.temporary.name)
        self.audio=self.folder/'song.wav';synthesize_audio(self.audio)
        self.studio=StudioController(ROOT,self.folder/'runtime')
        self.studio.chooseAudio(str(self.audio));wait_idle(self.studio)
        subtitles=self.folder/'song.srt';subtitles.write_text('1\n00:00:00,100 --> 00:00:00,700\n声音让我们靠近\n')
        self.studio.chooseSubtitles(str(subtitles));wait_idle(self.studio)

    def tearDown(self):
        self.studio.shutdown();APPLICATION.processEvents();self.temporary.cleanup()

    def image_source(self):
        path=self.folder/'background.png'
        image=QImage(160,90,QImage.Format_RGBA8888);image.fill(QColor('#3274aa'));self.assertTrue(image.save(str(path)))
        self.studio.chooseMaterial(str(path));wait_idle(self.studio)
        return self.studio.state['materialSources'][-1]['id']

    def test_advanced_slots_registered_and_catalogs_really_exposed(self):
        meta=self.studio.metaObject()
        signatures={bytes(meta.method(i).methodSignature()).decode() for i in range(meta.methodCount())}
        for prefix in ('addClip(','editClipRange(','setTransition(','generateConcertPlan(','editText(','analyzeAudio(','exportTextPNG(','analyzeConcertVideo('):
            self.assertTrue(any(value.startswith(prefix) for value in signatures),prefix)
        self.assertFalse(any(value.startswith(('recognizeLocal(', 'chooseAsrModel(')) for value in signatures))
        self.assertEqual([len(self.studio.state[key]) for key in ('templates','effects','layouts','transitions')],[16,8,184,53])

    def test_v2_project_migration_preserves_media_and_original_file(self):
        project=self.folder/'legacy.bfstudio.json';self.studio.saveProject(str(project))
        data=json.loads(project.read_text());data['version']=2
        data.pop('composition');data.pop('outputFormat')
        data['settings']={key:data['settings'][key] for key in ('fontSize','fontFamily','templateId','effectId','width','height')}
        project.write_text(json.dumps(data));before=project.read_bytes()
        self.studio.openProject(str(project))
        self.assertIn('工程已打开',self.studio.state['status'])
        self.assertEqual(project.read_bytes(),before)
        self.assertEqual(self.studio.state['settings']['layoutId'],'standard')
        self.assertFalse(self.studio.state['dirty'])

    def test_full_composition_undo_project_roundtrip_and_source_change_rejection(self):
        source=self.image_source();self.studio.addClip(source);self.studio.addText()
        text=self.studio.state['composition']['texts'][0]['id'];self.studio.editText(text,'text','演出开场')
        state=copy.deepcopy(self.studio.state['composition'])
        self.studio.undo();self.assertEqual(self.studio.state['composition']['texts'][0]['text'],'输入标题')
        self.studio.redo();self.assertEqual(self.studio.state['composition'],state)
        project=self.folder/'full.bfstudio.json';self.studio.saveProject(str(project));self.assertTrue(project.exists())
        self.studio.removeText(text);self.studio.openProject(str(project));self.assertEqual(self.studio.state['composition'],state)
        path=Path(state['sources'][source]['identity']['path']);path.write_bytes(b'changed image')
        self.studio.openProject(str(project));self.assertIn('源文件已变化',self.studio.state['status'])
        self.assertEqual(self.studio.state['composition'],state)

    def test_clip_lock_split_and_frame_aligned_transition(self):
        source=self.image_source();self.studio.addClip(source)
        clip=self.studio.state['composition']['clips'][0]['id']
        self.studio.editClip(clip,'locked',True);locked=copy.deepcopy(self.studio.state['composition'])
        self.studio.splitClip(clip,1000/24*10);self.assertEqual(self.studio.state['composition'],locked)
        self.studio.editClip(clip,'locked',False)
        self.studio.splitClip(clip,1000/24*10)
        rows=self.studio.state['composition']['clips'];self.assertEqual(len(rows),2,self.studio.state['status'])
        self.studio.setTransition(rows[1]['id'],'wipe-left',100)
        transition=self.studio.state['composition']['clips'][1]['transition']
        self.assertEqual(transition['id'],'wipe-left',self.studio.state['status'])
        self.assertAlmostEqual(transition['durationMs']*24/1000,2)

    def test_atomic_trim_undo_and_invalid_range_keeps_composition(self):
        source=self.image_source();self.studio.addClip(source)
        identifier=self.studio.state['composition']['clips'][0]['id']
        initial=copy.deepcopy(self.studio.state['composition'])
        self.studio.editClipRange(identifier,100,600,0)
        row=self.studio.state['composition']['clips'][0]
        self.assertEqual((row['startMs'],row['durationMs']),(100,600),self.studio.state['status'])
        trimmed=copy.deepcopy(self.studio.state['composition'])
        self.studio.editClipRange(identifier,700,300,0)
        self.assertEqual(self.studio.state['composition'],trimmed)
        self.studio.undo();self.assertEqual(self.studio.state['composition'],initial)

    def test_import_another_song_preserves_selected_fps_and_clears_analysis(self):
        self.studio.setSetting('fps',30)
        self.studio._state['concertAnalysis']={'sampledFrames':5}
        other=self.folder/'another.wav';synthesize_audio(other)
        self.studio.chooseAudio(str(other));wait_idle(self.studio)
        self.assertEqual(self.studio.state['composition']['fps'],30)
        self.assertEqual(self.studio.state['settings']['fps'],30)
        self.assertFalse(self.studio.state['concertAnalysis'])
        self.studio.addColorClip();self.studio.exportVideo();wait_idle(self.studio)
        self.assertTrue(self.studio.state['resultPath'],self.studio.state['status'])

    def test_lyric_split_merge_timing_and_non_overwriting_export(self):
        self.studio.splitSegment(0,400,3)
        self.assertEqual(len(self.studio.state['segments']),2,self.studio.state['status'])
        self.studio.mergeSegment(0);self.assertEqual(len(self.studio.state['segments']),1)
        self.studio.updateSegmentTiming(0,120,680)
        self.assertEqual(self.studio.state['segments'][0]['startMs'],120)
        output=self.folder/'lyrics.srt';self.studio.exportSubtitles('srt',str(output))
        self.assertIn('声音让我们靠近',output.read_text())
        before=output.read_bytes();self.studio.exportSubtitles('srt',str(output));self.assertEqual(output.read_bytes(),before)

    def test_preview_falls_back_to_legacy_image_in_clip_gaps(self):
        path=self.folder/'blue.png';image=QImage(160,90,QImage.Format_RGBA8888);image.fill(QColor('#0055ff'));image.save(str(path))
        self.studio.chooseBackground(str(path));self.studio.addColorClip()
        identifier=self.studio.state['composition']['clips'][0]['id']
        self.studio.editClip(identifier,'color','#ff0000');self.studio.editClip(identifier,'durationMs',300)
        provider=StudioPreviewProvider(self.studio)
        try:
            self.studio.seek(100);first=provider.requestImage('frame',QSize(),QSize()).pixelColor(10,100)
            self.studio.seek(500);gap=provider.requestImage('frame',QSize(),QSize()).pixelColor(10,100)
            self.assertGreater(first.red(),240);self.assertGreater(gap.blue(),240)
        finally:provider.shutdown()

    @unittest.skipUnless(find_ffmpeg(),'FFmpeg required')
    def test_preview_samples_legacy_video_in_gaps_at_actual_clock(self):
        path=self.folder/'red-blue.mp4'
        subprocess.run([find_ffmpeg(),'-v','error','-n','-f','lavfi','-i','color=c=red:s=160x90:r=24:d=0.4',
            '-f','lavfi','-i','color=c=blue:s=160x90:r=24:d=0.4','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]',
            '-map','[v]','-an','-c:v','libx264',str(path)],check=True,timeout=30)
        self.studio.chooseBackground(str(path));provider=StudioPreviewProvider(self.studio)
        try:
            self.studio.seek(100);first=provider.requestImage('frame',QSize(),QSize()).pixelColor(10,100)
            self.studio.seek(600);later=provider.requestImage('frame',QSize(),QSize()).pixelColor(10,100)
            self.assertGreater(first.red(),240);self.assertGreater(later.blue(),240)
        finally:provider.shutdown()

    def test_waveform_uses_real_pcm_and_analysis_worker(self):
        result=analyze_audio(self.audio,800,120)
        self.assertEqual(len(result['waveform']),1024)
        self.assertGreater(max(row['max'] for row in result['waveform']),.9)
        self.studio.analyzeAudio();wait_idle(self.studio)
        self.assertEqual(len(self.studio.state['waveform']),1024,self.studio.state['status'])
        self.assertTrue(self.studio.state['beatsMs'])
        waveform=copy.deepcopy(self.studio.state['waveform'])
        self.studio.addText();self.studio.undo()
        self.assertEqual(self.studio.state['waveform'],waveform)

    @unittest.skipUnless(find_ffmpeg(),'FFmpeg required')
    def test_concert_manual_review_apply_and_actual_full_export(self):
        video=self.folder/'concert.mp4'
        subprocess.run([find_ffmpeg(),'-v','error','-n','-f','lavfi','-i','testsrc2=s=160x90:r=24:d=1',
            '-an','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True,timeout=30)
        self.studio.chooseConcertVideo(str(video));wait_idle(self.studio)
        self.assertTrue(self.studio.state['concertSourceId'],self.studio.state['status'])
        subtitle=self.folder/'concert.srt';subtitle.write_text('1\n00:00:00,200 --> 00:00:00,800\n声音让我们靠近\n')
        self.studio.chooseConcertSubtitles(str(subtitle));self.studio.generateConcertPlan()
        self.assertTrue(self.studio.state['concertRows'],self.studio.state['status'])
        self.assertAlmostEqual(self.studio.state['concertCoverage']['coverageRatio'],1)
        self.assertTrue(self.studio.state['dirty'])
        original=copy.deepcopy(self.studio.state['concertRows'])
        self.studio.reviewConcertRow(0,'durationMs',50);self.studio.setConcertReviewed(True)
        self.studio.applyConcertPlan();self.assertFalse(self.studio.state['composition']['clips'])
        self.assertIn('空档',self.studio.state['status'])
        self.studio.undo();self.studio.undo();self.assertEqual(self.studio.state['concertRows'],original)
        self.studio.reviewConcertRow(0,'reviewed',True)
        candidate=copy.deepcopy(self.studio.state['concertRows'])
        project=self.folder/'candidate.bfstudio.json';self.studio.saveProject(str(project))
        self.assertTrue(project.is_file(),self.studio.state['status'])
        self.studio.setConcertReviewed(True);self.studio.openProject(str(project))
        self.assertEqual(self.studio.state['concertRows'],candidate,self.studio.state['status'])
        self.assertFalse(self.studio.state['composition']['clips'])
        self.studio.applyConcertPlan();self.assertFalse(self.studio.state['composition']['clips'])
        self.studio.setConcertReviewed(True);self.studio.applyConcertPlan()
        self.assertTrue(self.studio.state['composition']['clips'],self.studio.state['status'])
        self.assertTrue(self.studio.state['concertReviewed'])
        self.studio.undo();self.assertFalse(self.studio.state['composition']['clips'])
        self.assertTrue(self.studio.state['concertRows'])
        self.studio.redo();self.assertTrue(self.studio.state['composition']['clips'])
        self.studio.addText();self.studio.exportVideo();wait_idle(self.studio)
        output=Path(self.studio.state['resultPath']);self.assertTrue(output.is_file(),self.studio.state['status'])
        self.assertTrue(any(row['codec_type']=='video' for row in probe_media(output,verify_video=True)['streams']))
        self.studio.removeClip(self.studio.state['composition']['clips'][-1]['id'])
        self.assertFalse(self.studio.state['concertCoverage']['fullCoverage'])
        provider=StudioPreviewProvider(self.studio)
        try:
            self.studio.seek(400);frame=provider.requestImage('frame',QSize(),QSize())
            self.assertEqual(frame.size(),QSize(854,480))
        finally:provider.shutdown()
        self.studio.exportVideo();wait_idle(self.studio)
        self.assertTrue(self.studio.state['resultPath'],self.studio.state['status'])
        self.studio.updateSegment(0,'新的歌词需要复核');self.assertFalse(self.studio.state['concertReviewed'])

    @unittest.skipUnless(find_ffmpeg(),'FFmpeg required')
    def test_regenerated_concert_preview_source_replacement_and_new_subtitles(self):
        video=self.folder/'concert.mp4'
        subprocess.run([find_ffmpeg(),'-v','error','-n','-f','lavfi','-i','color=c=blue:s=160x90:r=24:d=1',
            '-an','-c:v','libx264',str(video)],check=True,timeout=30)
        self.studio.chooseConcertVideo(str(video));wait_idle(self.studio)
        subtitle=self.folder/'concert.srt';subtitle.write_text('1\n00:00:00,200 --> 00:00:00,800\n声音让我们靠近\n')
        self.studio.chooseConcertSubtitles(str(subtitle));self.studio.generateConcertPlan()
        self.studio.setConcertReviewed(True);self.studio.applyConcertPlan()
        self.assertFalse(self.studio.state['concertHasDraft'])
        self.studio.generateConcertPlan();self.assertTrue(self.studio.state['concertHasDraft'])
        paired=next(index for index,row in enumerate(self.studio.state['concertRows']) if row['purpose']=='paired')
        provider=StudioPreviewProvider(self.studio)
        try:
            self.studio.seek(400);frame=provider.requestImage('frame?concertRow='+str(paired),QSize(),QSize())
            self.assertGreater(frame.pixelColor(10,100).blue(),240)
        finally:provider.shutdown()
        self.studio.addText();self.assertTrue(self.studio.state['concertHasDraft'])
        image=self.image_source();self.assertTrue(self.studio.state['concertHasDraft'])
        self.studio.reviewConcertRow(paired,'sourceId',image)
        row=self.studio.state['concertRows'][paired]
        self.assertEqual((row['kind'],row['purpose'],row['sourceInMs'],row['rate']),('image','cutaway',0,1))
        self.assertFalse(row['reviewed'])
        self.studio.reviewConcertRow(paired,'sourceId','')
        self.assertEqual(self.studio.state['concertRows'][paired]['kind'],'color')
        self.studio.undo();self.assertEqual(self.studio.state['concertRows'][paired]['kind'],'image')
        changed=self.folder/'changed.srt';changed.write_text('1\n00:00:00,200 --> 00:00:00,800\n重新导入来源歌词\n')
        self.studio.chooseConcertSubtitles(str(changed))
        self.assertEqual(self.studio.state['composition']['concert']['sourceSegments'][0]['text'],'重新导入来源歌词',self.studio.state['status'])
        self.assertFalse(self.studio.state['concertRows'])
        self.assertFalse(self.studio.state['concertHasDraft'])
        self.assertTrue(self.studio.state['composition']['clips'])
        self.assertNotIn('purpose',self.studio.state['composition']['clips'][0])

    def test_transparent_text_png_and_alpha_export_format_roundtrip(self):
        self.studio.addText();identifier=self.studio.state['composition']['texts'][0]['id']
        self.studio.editText(identifier,'text','透明文字')
        output=self.folder/'text.png';self.studio.exportTextPNG(identifier,str(output))
        image=QImage(str(output));self.assertFalse(image.isNull(),self.studio.state['status'])
        self.assertEqual(image.pixelColor(0,0).alpha(),0)
        self.assertTrue(any(image.pixelColor(x,240).alpha()>0 for x in range(854)))
        self.studio.setOutputFormat('mov-alpha');self.assertEqual(self.studio.state['outputFormat'],'mov-alpha')
        self.studio.undo();self.assertEqual(self.studio.state['outputFormat'],'mp4')
        self.studio.redo();self.assertEqual(self.studio.state['outputFormat'],'mov-alpha')

    @unittest.skipUnless(find_ffmpeg(),'FFmpeg required')
    def test_actual_alpha_movie_has_transparent_pixels(self):
        self.studio.setOutputFormat('mov-alpha');self.studio.exportVideo();wait_idle(self.studio)
        path=self.studio.state['resultPath'];self.assertTrue(path,self.studio.state['status'])
        self.assertEqual(Path(path).suffix,'.mov')
        result=subprocess.run([find_ffmpeg(),'-v','error','-ss','0.4','-i',path,'-vf','alphaextract','-frames:v','1',
            '-f','rawvideo','-pix_fmt','gray','pipe:1'],capture_output=True,check=True,timeout=30)
        self.assertEqual(len(result.stdout),854*480)
        self.assertEqual(min(result.stdout),0);self.assertGreater(max(result.stdout),240)

    def test_save_video_as_preserves_bytes_and_does_not_overwrite_existing_file(self):
        self.studio.exportVideo();wait_idle(self.studio)
        self.assertTrue(self.studio.state['resultPath'], self.studio.state['status'])
        original=Path(self.studio.state['resultPath']).read_bytes()
        target=self.folder/'成品.mp4';self.studio.saveVideoAs(str(target))
        self.assertEqual(target.read_bytes(),original,self.studio.state['status'])
        self.assertEqual(self.studio.state['savedVideoPath'],str(target))
        self.studio.saveVideoAs(str(target));self.assertEqual(target.read_bytes(),original)
        self.assertTrue(self.studio.state['error'])
        other=self.folder/'失败的成品.mp4'
        with patch('shutil.copyfileobj',side_effect=OSError('模拟磁盘写入失败')):
            self.studio.saveVideoAs(str(other))
        self.assertFalse(other.exists())
        self.assertEqual(Path(self.studio.state['resultPath']).read_bytes(),original)

    @unittest.skipUnless(find_ffmpeg(),'FFmpeg required')
    def test_video_motion_inspection_detects_actual_stationary_frames(self):
        video=self.folder/'still.mp4'
        subprocess.run([find_ffmpeg(),'-v','error','-n','-f','lavfi','-i','color=c=blue:s=160x90:r=24:d=4',
            '-an','-c:v','libx264',str(video)],check=True,timeout=30)
        data=inspect_video(video,4000)
        self.assertEqual(data['sampledFrames'],4)
        self.assertTrue(data['stationaryIntervals'])
        self.assertEqual(data['sceneCutsMs'],[])
        self.assertGreaterEqual(data['stationaryIntervals'][0]['endMs'],2000)


if __name__=='__main__':unittest.main()
