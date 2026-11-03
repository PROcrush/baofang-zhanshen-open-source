"""Inspect real decoded concert frames for scene cuts and stationary intervals."""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from worker.studio_render import find_ffmpeg


def inspect_video(path,duration_ms):
    if not isinstance(duration_ms,(int,float)) or not math.isfinite(duration_ms) or not 0<duration_ms<=43200000:
        raise ValueError('视频分析时长无效')
    ffmpeg=find_ffmpeg()
    if not ffmpeg:raise ValueError('缺少通用FFmpeg')
    size=64*36
    process=subprocess.Popen([ffmpeg,'-v','error','-i',str(Path(path).resolve(strict=True)),
        '-an','-vf','fps=1,scale=64:36','-pix_fmt','gray','-f','rawvideo','pipe:1'],
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    previous=None;index=0;still_start=None;stationary=[];cuts=[];motion=[]
    try:
        while True:
            frame=process.stdout.read(size)
            if not frame:break
            if len(frame)!=size:raise ValueError('视频没有返回完整采样帧')
            if previous is not None:
                delta=sum(abs(left-right) for left,right in zip(previous,frame))/size
                start=(index-1)*1000;end=min(duration_ms,index*1000)
                motion.append({'startMs':start,'endMs':end,'meanChange':round(delta,4)})
                if delta<.8:
                    if still_start is None:still_start=start
                elif still_start is not None:
                    if start-still_start>=2000:stationary.append({'startMs':still_start,'endMs':start})
                    still_start=None
                if delta>32:cuts.append(index*1000)
            previous=frame;index+=1
        if process.wait(timeout=30):raise ValueError('视频画面分析解码失败')
        if not index:raise ValueError('没有有效视频采样帧')
        if still_start is not None and min(index*1000,duration_ms)-still_start>=2000:
            stationary.append({'startMs':still_start,'endMs':min(index*1000,duration_ms)})
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        process.stdout.close()
    return {'sampledFrames':index,'sampleRate':1,'sourceDurationMs':duration_ms,
            'motionIntervals':motion,'stationaryIntervals':stationary,'sceneCutsMs':cuts,
            'notice':'实际解码画面的1fps运动检查；静止可能是有意构图，需人工复核，不是内容审核或逐帧验收。'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);args=parser.parse_args()
    try:
        data=json.loads(Path(args.request).read_text())
        print(json.dumps(inspect_video(data['path'],data['durationMs']),ensure_ascii=False));return 0
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print(json.dumps({'type':'error','message':str(error)},ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())
