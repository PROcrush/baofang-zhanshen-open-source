from app.karaoke import lyric_frame, sweep_cursor, token_cursor, traditional_lines


def project_lyrics(timeline, position_ms, *, traditional=False, sweep=False):
    """Query original-source milliseconds, including no-lyric gaps."""
    hidden = {'text': '', 'nextText': '', 'progress': 0.0, 'quality': 'unavailable', 'visible': False,
              'countdownMs': 0}
    if not timeline:
        return hidden
    segments = timeline['segments']
    if traditional:
        rows = traditional_lines([dict(row, characters=row.get('words', []) if timeline['quality'] == 'word' else [])
                                  for row in segments])
        frame = lyric_frame(rows, position_ms, pre_display=True)
        if not frame:
            return hidden
        row, following = frame['current'], frame['following']
        timed_words = token_cursor(row, position_ms) is not None
        cursor = sweep_cursor(row, position_ms)
        progress = (cursor[0] + (cursor[1] - cursor[0]) * cursor[2]) / max(1, len(row['text'])) if cursor else 0.
        return {'text': row['text'], 'nextText': following['text'] if following else '',
                'progress': progress, 'quality': 'word' if timed_words else 'sentence',
                'sweepTiming': 'word' if timed_words else 'sentence',
                'visible': True, 'countdownMs': 0, 'waiting': frame['waiting'], 'karaoke': frame}
    for i, segment in enumerate(segments):
        if segment['startMs'] <= position_ms < segment['endMs']:
            effect_clock = dict(startMs=segment['startMs'], endMs=segment['endMs'], positionMs=position_ms)
            if sweep:
                row = dict(segment, characters=segment.get('words', []) if timeline['quality']=='word' else [])
                cursor = sweep_cursor(row, position_ms)
                timed = token_cursor(row, position_ms) is not None
                return {'text':segment['text'], 'nextText':segments[i+1]['text'] if i+1<len(segments) else '',
                        'progress':(cursor[0]+(cursor[1]-cursor[0])*cursor[2])/max(1,len(segment['text'])),
                        'sweepCursor':list(cursor), 'sweepTiming':'word' if timed else 'sentence',
                        'quality':'word' if timed else 'sentence', 'visible':True, 'countdownMs':0,
                        'effectClock':effect_clock}
            progress = 0.0
            if timeline['quality'] == 'word':
                total = len(segment['text'])
                completed = 0.0
                for word in segment['words']:
                    if position_ms >= word['endMs']:
                        completed += len(word['text'])
                    elif position_ms >= word['startMs']:
                        completed += len(word['text']) * (position_ms - word['startMs']) / (word['endMs'] - word['startMs'])
                progress = completed / total if total else 0.0
            return {'text': segment['text'], 'nextText': segments[i+1]['text'] if i+1 < len(segments) else '',
                    'progress': max(0.0, min(1.0, progress)), 'quality': timeline['quality'], 'visible': True,
                    'countdownMs': 0, 'effectClock': effect_clock}
        if position_ms < segment['startMs']:
            return dict(hidden, nextText=segment['text'], countdownMs=segment['startMs'] - position_ms)
    return hidden
