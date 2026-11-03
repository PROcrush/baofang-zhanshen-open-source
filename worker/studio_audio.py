"""Stream real PCM into a bounded waveform and an onset-anchored BPM grid."""
import argparse
import array
import json
import math
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from worker.studio_render import find_ffmpeg


def analyze_audio(path,duration_ms,bpm=120):
    if not math.isfinite(duration_ms) or duration_ms<=0 or not 40<=bpm<=240:
        raise ValueError('音频时长或BPM无效')
    ffmpeg=find_ffmpeg()
    if not ffmpeg:raise ValueError('缺少通用FFmpeg')
    # 50 envelopes per second, independent of output video size and source rate.
    count=1024;peaks=[0.0]*count;troughs=[0.0]*count;maximum_energy=[0.0]*count
    rate=16000;block=320;index=0;strongest=(0.0,0.0);previous=0.0
    process=subprocess.Popen([ffmpeg,'-v','error','-i',str(Path(path).resolve(strict=True)),
        '-map','0:a:0','-ac','1','-ar',str(rate),'-f','f32le','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    try:
        while True:
            raw=process.stdout.read(block*4)
            if not raw:break
            raw=raw[:len(raw)//4*4]
            samples=array.array('f');samples.frombytes(raw)
            if not samples:break
            ms=index*block/rate*1000
            bucket=min(count-1,int(ms/duration_ms*count))
            lo,hi=min(samples),max(samples)
            energy=math.sqrt(sum(sample*sample for sample in samples)/len(samples))
            if not all(math.isfinite(v) for v in (lo,hi,energy)):raise ValueError('音频包含无效PCM数值')
            peaks[bucket]=max(peaks[bucket],hi);troughs[bucket]=min(troughs[bucket],lo)
            maximum_energy[bucket]=max(maximum_energy[bucket],energy)
            flux=max(0,energy-previous)
            # Grid phase is inferred from a strong observed onset in the first 20s.
            if ms<min(20000,duration_ms) and flux>strongest[0]:strongest=(flux,ms)
            previous=energy;index+=1
        if process.wait(timeout=20):raise ValueError('歌曲PCM解码失败')
        if not index:raise ValueError('没有可读取的音频样本')
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        process.stdout.close()
    normal=max(max(peaks),abs(min(troughs)),1e-9)
    waveform=[{'min':round(lo/normal,5),'max':round(hi/normal,5),'energy':round(min(1,e/normal),5)}
              for lo,hi,e in zip(troughs,peaks,maximum_energy)]
    period=60000/bpm;phase=strongest[1]%period if strongest[0]>1e-5 else 0
    beats=[round(phase+step*period,3) for step in range(int((duration_ms-phase)/period)+1) if phase+step*period<duration_ms]
    return {'waveform':waveform,'beatsMs':beats,'bpm':bpm,'anchorMs':strongest[1],
            'notice':'波形来自实际PCM；节拍按设置BPM与检测起点生成，可人工校正，不是已核验的DJ拍点。'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True)
    args=parser.parse_args()
    try:
        request=json.loads(Path(args.request).read_text())
        print(json.dumps(analyze_audio(request['path'],request['durationMs'],request['bpm']),ensure_ascii=False))
        return 0
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print(json.dumps({'type':'error','message':str(error)},ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())
