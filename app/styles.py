"""Validated live subtitle preferences shared by preview and both output paths."""
import math
import re

from app.style_catalog import DECORATION_MODES, preset_definition, preset_scheme_count
from app.live_effects import LIVE_EFFECTS
from worker.style_catalog import TRADITIONAL_KARAOKE_STYLE

# Keep countdownEnabled readable for old settings/output payloads; renderers no longer use it.
LEGACY_FIELDS = frozenset(('fontFamily', 'fontSize', 'letterSpacing', 'brightness',
                          'sweepMode', 'outputMode', 'dynamicBackground', 'countdownEnabled'))
DEFAULT_STYLE = {'fontFamily': 'PingFang SC', 'fontSize': 48, 'letterSpacing': 0,
                 'brightness': 1, 'sweepMode': 'word', 'outputMode': 'standard',
                 'dynamicBackground': False, 'countdownEnabled': False,
                 'liveEntrance': 'none', 'liveExit': 'none',
                 'baseColor': '#F0F1F3', 'highlightColor': '#F0C17A', 'nextColor': '#929AA7',
                 'outlineColor': '#070B12', 'outlineWidth': 1,
                 'shadowEnabled': False, 'shadowColor': '#000000', 'shadowBlur': 6,
                 'shadowOffsetX': 0, 'shadowOffsetY': 3,
                 'verticalPosition': 56, 'horizontalMargin': 4, 'lineMode': 'double',
                 'fontBold': True, 'lineSpacing': 23, 'textAlignment': 'center',
                 'presetId': 'custom', 'schemeIndex': 0, 'presetCustomized': False,
                 'decorationMode': 'none', 'decorationEnabled': True,
                 'decorationColor': '#16F4D4', 'backgroundColor': '#000000',
                 'gradientEnabled': False, 'gradientColor': '#F0F1F3'}
STYLE_PRESETS = {
    'classic': {'baseColor': TRADITIONAL_KARAOKE_STYLE['textColor'], 'highlightColor': TRADITIONAL_KARAOKE_STYLE['highlightColor'], 'nextColor': TRADITIONAL_KARAOKE_STYLE['textColor'],
                'outlineColor': TRADITIONAL_KARAOKE_STYLE['strokeColor'], 'outlineWidth': TRADITIONAL_KARAOKE_STYLE['strokeWidth'], 'shadowEnabled': True,
                'shadowColor': '#000000', 'shadowBlur': 6, 'shadowOffsetX': 0,
                'shadowOffsetY': 3, 'fontSize': 48, 'letterSpacing': 1,
                'verticalPosition': 78, 'horizontalMargin': 7, 'lineMode': 'karaoke',
                'sweepMode': 'word', 'outputMode': 'standard'},
    'gold': {'baseColor': '#FFF4DA', 'highlightColor': '#FFD26B', 'nextColor': '#BDAF95',
             'outlineColor': '#20150A', 'outlineWidth': 2, 'shadowEnabled': True,
             'shadowColor': '#000000', 'shadowBlur': 8, 'shadowOffsetX': 0,
             'shadowOffsetY': 3, 'fontSize': 56, 'letterSpacing': 1,
             'verticalPosition': 72, 'horizontalMargin': 7, 'lineMode': 'double',
             'sweepMode': 'sentence', 'outputMode': 'standard'},
    'neon': {'baseColor': '#E9F9FF', 'highlightColor': '#6AE4FF', 'nextColor': '#819EBD',
             'outlineColor': '#0A1830', 'outlineWidth': 2, 'shadowEnabled': True,
             'shadowColor': '#163865', 'shadowBlur': 10, 'shadowOffsetX': 0,
             'shadowOffsetY': 3, 'fontSize': 52, 'letterSpacing': .5,
             'verticalPosition': 70, 'horizontalMargin': 6, 'lineMode': 'karaoke',
             'sweepMode': 'word', 'outputMode': 'standard'},
    'minimal': {'baseColor': '#FFFFFF', 'highlightColor': '#FFFFFF', 'nextColor': '#B0B8C3',
                'outlineColor': '#000000', 'outlineWidth': 1, 'shadowEnabled': True,
                'shadowColor': '#000000', 'shadowBlur': 4, 'shadowOffsetX': 0,
                'shadowOffsetY': 2, 'fontSize': 52, 'letterSpacing': 0,
                'verticalPosition': 76, 'horizontalMargin': 8, 'lineMode': 'single',
                'sweepMode': 'sentence', 'outputMode': 'standard'},
}


def validate_style(value):
    """Upgrade old local preferences; never silently accept unknown output fields."""
    if not isinstance(value, dict) or not LEGACY_FIELDS.issubset(value) or set(value) - set(DEFAULT_STYLE):
        raise ValueError('字幕样式字段无效')
    result = dict(DEFAULT_STYLE, **value)
    for key, low, high in (('fontSize', 24, 96), ('letterSpacing', 0, 8), ('brightness', .35, 1),
                           ('outlineWidth', 0, 8), ('shadowBlur', 0, 16),
                           ('shadowOffsetX', -12, 12), ('shadowOffsetY', -12, 12),
                           ('verticalPosition', 8, 90), ('horizontalMargin', 2, 20), ('lineSpacing', 0, 64)):
        number = result[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not low <= number <= high:
            raise ValueError('字幕样式数值超出范围：' + key)
    if not isinstance(result['fontFamily'], str) or not result['fontFamily'].strip() or len(result['fontFamily']) > 96:
        raise ValueError('字体名称无效')
    if result['sweepMode'] not in ('word', 'sentence') or result['outputMode'] not in ('standard', 'dynamic') or result['lineMode'] not in ('single', 'double', 'karaoke') or result['textAlignment'] not in ('left', 'center', 'right'):
        raise ValueError('字幕显示模式无效')
    if any(result[key] not in LIVE_EFFECTS for key in ('liveEntrance', 'liveExit')):
        raise ValueError('请选择可用的字幕入场或退场效果')
    for key in ('dynamicBackground', 'countdownEnabled', 'shadowEnabled', 'fontBold',
                'presetCustomized', 'decorationEnabled', 'gradientEnabled'):
        if type(result[key]) is not bool:
            raise ValueError('字幕开关类型无效')
    for key in ('baseColor', 'highlightColor', 'nextColor', 'outlineColor', 'shadowColor',
                'decorationColor', 'backgroundColor', 'gradientColor'):
        if not isinstance(result[key], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', result[key]):
            raise ValueError('字幕颜色须为 #RRGGBB：' + key)
    if not isinstance(result['decorationMode'], str) or result['decorationMode'] not in ('none', *DECORATION_MODES):
        raise ValueError('字幕装饰模式无效')
    preset_id, scheme_index = result['presetId'], result['schemeIndex']
    if not isinstance(preset_id, str):
        raise ValueError('字幕预设无效')
    if type(scheme_index) is not int or not 0 <= scheme_index < preset_scheme_count(preset_id):
        raise ValueError('字幕配色序号无效')
    return result


def changed_style(style, key, value):
    candidate = validate_style(style)
    if not isinstance(key, str) or key not in DEFAULT_STYLE:
        raise ValueError('不支持的字幕样式')
    if key in ('presetId', 'schemeIndex', 'presetCustomized'):
        raise ValueError('请通过预设选择器切换样式与配色')
    candidate[key] = value
    if key not in ('brightness', 'countdownEnabled', 'liveEntrance', 'liveExit'):
        candidate['presetCustomized'] = True
    return validate_style(candidate)


def traditional_style(current):
    """One-click traditional KTV appearance shared with the studio preset."""
    recipe = TRADITIONAL_KARAOKE_STYLE
    style = validate_style(current)
    return validate_style(dict(style, fontFamily=recipe['fontFamily'], fontSize=recipe['fontSize'],
        fontBold=recipe['fontBold'], letterSpacing=recipe['letterSpacing'],
        baseColor=recipe['textColor'], nextColor=recipe['textColor'], highlightColor=recipe['highlightColor'],
        outlineColor=recipe['strokeColor'], outlineWidth=recipe['strokeWidth'], shadowEnabled=recipe['shadow'],
        verticalPosition=78, horizontalMargin=7, lineSpacing=23,
        lineMode='karaoke', outputMode='standard', sweepMode='word', countdownEnabled=False,
        gradientEnabled=False, decorationMode='none', decorationEnabled=False,
        presetId='classic', schemeIndex=0, presetCustomized=False))


def style_preset(name, current=None, scheme_index=0):
    """Apply an actual renderer recipe while retaining venue brightness and timing."""
    previous = validate_style(DEFAULT_STYLE if current is None else current)
    if not isinstance(name, str):
        raise ValueError('字幕预设无效')
    if type(scheme_index) is not int or not 0 <= scheme_index < preset_scheme_count(name):
        raise ValueError('字幕配色序号无效')
    reset = {key: DEFAULT_STYLE[key] for key in (
        'decorationMode', 'decorationEnabled', 'decorationColor', 'backgroundColor',
        'gradientEnabled', 'gradientColor')}
    reset.update(presetId=name, schemeIndex=scheme_index, presetCustomized=False)
    if name in STYLE_PRESETS:
        # The four familiar quick choices keep the operator's font/background.
        appearance = dict(STYLE_PRESETS[name], fontBold=True, lineSpacing=23,
                          textAlignment='center', **reset)
        return validate_style(dict(previous, **appearance))

    definition = preset_definition(name)
    scheme = definition['schemes'][scheme_index]
    mode = definition['decorationMode']
    paper = scheme.get('paper', False)
    neon = mode in ('ocean', 'vapor', 'synth80', 'acid', 'mint')
    serif = definition['fontKind'] in ('mincho', 'hand', 'brush')
    gradient = scheme.get('grad')
    appearance = dict(reset,
        decorationMode=mode, fontFamily='Songti SC' if serif else 'PingFang SC',
        fontBold=definition['fontWeight'] >= 600,
        fontSize=56 if mode in ('gold', 'candy', 'magenta', 'sunset') else 52,
        letterSpacing=1 if serif or mode in ('mint', 'hud', 'hrNightRec') else 0,
        baseColor=gradient[0] if gradient else scheme['fg'],
        highlightColor=scheme['accent'], nextColor=scheme['sub'],
        backgroundColor=scheme['bg'], decorationColor=scheme['accent2'],
        gradientEnabled=bool(gradient), gradientColor=gradient[1] if gradient else scheme['fg'],
        outlineColor=scheme['bg'], outlineWidth=1 if paper else 2,
        shadowEnabled=not paper, shadowColor=scheme['accent2'] if neon else scheme['bg'],
        shadowBlur=10 if neon else 6, shadowOffsetX=0, shadowOffsetY=3,
        verticalPosition=70, horizontalMargin=7,
        lineMode='single' if mode in ('specimen', 'hrCurse', 'sumi') else 'double',
        lineSpacing=24, textAlignment='center', sweepMode='word', outputMode='standard',
        dynamicBackground=True)
    return validate_style(dict(previous, **appearance))
