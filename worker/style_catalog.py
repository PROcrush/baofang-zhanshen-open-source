"""Original, executable style presets for the independent CPU renderer.

Layouts are parameter variations of eight motion families, not 184 algorithms.
Transitions are thirteen algorithms in four directions, plus a hard cut.
No catalog data or media is read from an application bundle.
"""

import math
import re


_TEMPLATE_ROWS = [
    ('none', '留白双行', '无底板主句与下一句，干净耐看', '清爽', True),
    ('headline', '封面大字', '居中大字分行，适合歌曲封面', '设计', True),
    ('minimal', '电影字幕', '下方轻描边白字，把空间留给画面', '清爽', True),
    ('neon', '薄荷夜场', '近白与薄荷色交错叠排，无边框和进度装饰', '设计', True),
    ('sidecard', '左下歌签', '左下两行留白，适合演唱会画面', '清爽', False),
    ('banner', '顶部轻题', '顶部小号文字，避开画面主体', '清爽', False),
    ('stacked', '短句海报', '紧凑叠排与清晰字重，适合短歌词', '设计', False),
    ('split', '对唱分屏', '当前句与下一句分列两侧', '设计', False),
    ('vertical', '竖排留白', '右侧双列排字，长句自动折列', '设计', False),
    ('poster', '杂志封面', '左侧大字与小号预告，疏密有序', '设计', True),
    ('ribbon', '强调字卡', '贴合文字的浅色圆角字卡', '设计', True),
    ('karaoke', '传统 KTV', '粗描边大字、黄白扫色、上下两行轮唱；保留真实字词时码', '跟唱', True),
    ('karaoke-card', '简洁跟唱', '轻底板双行，按真实字词时码扫色', '跟唱', True),
    ('spotlight', '柔光焦点', '轻柔暗色遮罩与中央短句', '设计', False),
    ('stage', '现场大屏', '大字双行，远距离也能看清', '清爽', False),
    ('kinetic', '轻动标题', '短促上浮入场，停稳后清晰阅读', '设计', False),
]
TEMPLATES = [{'id': key, 'label': label, 'description': description,
              'category': category, 'recommended': recommended,
              'parameters': {'design': key}} for key, label, description, category, recommended in _TEMPLATE_ROWS]

TRADITIONAL_KARAOKE_STYLE = dict(fontFamily='Heiti SC', fontSize=48, fontBold=True,
    letterSpacing=1, lineSpacing=1.25, textColor='#F7F7F2', highlightColor='#FFE600',
    strokeColor='#101016', strokeWidth=3, shadow=True, x=.5, y=.78)


_TEMPLATE_STYLES = {
    'none': dict(fontSize=36),
    'headline': dict(fontSize=64, fontBold=True, letterSpacing=1),
    'minimal': dict(fontSize=30, strokeWidth=1.2),
    'neon': dict(fontSize=62, fontBold=True, highlightColor='#B7F5DB'),
    'sidecard': dict(fontSize=34, fontBold=True),
    'banner': dict(fontSize=28, letterSpacing=1),
    'stacked': dict(fontSize=58, fontBold=True, lineSpacing=1.12),
    'split': dict(fontSize=34, fontBold=True),
    'vertical': dict(fontFamily='Songti SC', fontSize=34, lineSpacing=1.3, strokeWidth=.6),
    'poster': dict(fontSize=60, fontBold=True, letterSpacing=1),
    'ribbon': dict(fontSize=38, fontBold=True, textColor='#22232A', strokeWidth=0, shadow=False),
    'karaoke': TRADITIONAL_KARAOKE_STYLE,
    'karaoke-card': dict(fontSize=40, fontBold=True, highlightColor='#B7F5DB', strokeWidth=.8),
    'spotlight': dict(fontSize=48),
    'stage': dict(fontSize=56, fontBold=True, strokeWidth=1.4),
    'kinetic': dict(fontSize=62, fontBold=True, entranceId='rise', fadeMs=200),
}


def template_settings(current, identifier):
    """A reversible visual preset; keep output size, media and timing untouched."""
    find_style(TEMPLATES, identifier)
    settings = dict(current, templateId=identifier, fontFamily='PingFang SC', fontSize=38,
                    fontBold=False, letterSpacing=0, lineSpacing=1.25,
                    textColor='#F4F3F0', highlightColor='#B7F5DB', strokeColor='#15171C',
                    strokeWidth=.8, shadow=True, x=.5, y=.78, effectId='none',
                    entranceId='none', exitId='none', fadeMs=0,
                    layoutId='standard', layoutParameters={})
    settings.update(_TEMPLATE_STYLES[identifier])
    if settings.get('lyricSweep', identifier in ('karaoke','karaoke-card')):
        from app.karaoke import sweep_color
        settings['highlightColor'] = sweep_color(settings['textColor'], settings['highlightColor'])
    return settings

EFFECTS = [
    {'id': 'none', 'label': '无额外动画', 'description': '保持文字位置与不透明度，KTV 模板仍按时码扫色'},
    {'id': 'fade', 'label': '淡入淡出', 'description': '片段两端渐变不透明度'},
    {'id': 'pulse', 'label': '文字呼吸', 'description': '连续改变文字缩放'},
    {'id': 'rise', 'label': '上升入场', 'description': '从下方向目标位置移动'},
    {'id': 'drift', 'label': '横向漂移', 'description': '随时间横向缓慢移动'},
    {'id': 'typewriter', 'label': '逐字显现', 'description': '按视觉动画进度揭示文字，不创建歌词时码'},
    {'id': 'glow', 'label': '光晕流动', 'description': '随时间改变文字光晕强度'},
    {'id': 'bounce', 'label': '轻弹入场', 'description': '阻尼弹跳到目标位置'},
]

_MOTIONS = [('orbit', '环绕'), ('float', '浮动'), ('sway', '摆动'), ('zoom', '缩放'),
            ('rotate', '旋转'), ('wave', '波动'), ('step', '步进'), ('beat', '节拍')]
LAYOUTS = []
for family_index, (motion, label) in enumerate(_MOTIONS):
    for variant in range(23):
        standard = family_index == 0 and variant == 0
        LAYOUTS.append({
            'id': 'standard' if standard else f'{motion}-{variant + 1:02d}',
            'label': '标准静态布局' if standard else f'{label} · {variant + 1:02d}',
            'description': '固定排版' if standard else f'{label}算法的独立位置、幅度、相位与缩放参数组合',
            'family': motion,
            'parameters': {
                'motion': 'static' if standard else motion,
                'offsetX': 0 if standard else ((variant % 5) - 2) * .055,
                'offsetY': 0 if standard else ((variant // 5) - 2) * .038,
                'amplitude': 0 if standard else .012 + (variant % 7) * .008,
                'frequency': .7 + (variant % 6) * .23,
                'phase': variant * .47 + family_index * .31,
                'scale': 1 if standard else .86 + (variant % 8) * .04,
                'rotation': 0 if standard else ((variant % 5) - 2) * 1.8,
            },
        })

_ALGORITHMS = [('fade', '方向溶解'), ('wipe', '推移遮罩'), ('slide', '滑入'), ('push', '推屏'),
               ('zoom', '边缘缩放'), ('iris', '圆形展开'), ('blinds', '百叶'), ('checker', '棋格'),
               ('split', '对开'), ('diagonal', '斜切'), ('dissolve', '颗粒溶解'),
               ('pixelate', '像素展开'), ('clock', '时钟扫描')]
_DIRECTIONS = [('left', '左'), ('right', '右'), ('up', '上'), ('down', '下')]
TRANSITIONS = [{'id': 'cut', 'label': '直接切换', 'description': '边界直接换镜',
                'algorithm': 'cut', 'direction': 'left', 'parameters': {}}]
for algorithm, label in _ALGORITHMS:
    for direction, direction_label in _DIRECTIONS:
        TRANSITIONS.append({'id': f'{algorithm}-{direction}', 'label': f'{label} · {direction_label}',
                            'description': f'{label}算法，{direction_label}方向独立遮罩或轨迹',
                            'algorithm': algorithm, 'direction': direction,
                            'parameters': {'cells': 12, 'softness': .18}})

_TEXT_ROWS = [('standard', '居中文字', .5, .5, 0), ('title', '顶部标题', .5, .16, 0),
              ('lower-third', '下方标题', .5, .84, 0), ('badge', '角部标签', .18, .13, 0),
              ('vertical', '竖排文字', .86, .5, 1), ('captions', '底部注释', .5, .92, 0),
              ('centered', '中心卡片', .5, .45, 0), ('top-left', '左上文字', .22, .22, 0),
              ('top-right', '右上文字', .78, .22, 0), ('bottom-left', '左下文字', .22, .78, 0),
              ('bottom-right', '右下文字', .78, .78, 0), ('credits', '片尾叠排', .5, .64, 2)]
TEXT_LAYOUTS = [{'id': key, 'label': label, 'description': label + '的原创文本排版',
                 'parameters': {'x': x, 'y': y, 'vertical': mode == 1, 'multiline': mode == 2}}
                for key, label, x, y, mode in _TEXT_ROWS]

DEFAULT_SETTINGS = {'fontSize': 42, 'fontFamily': 'PingFang SC', 'templateId': 'none',
                    'effectId': 'none', 'width': 854, 'height': 480, 'layoutId': 'standard',
                    'textColor': '#ffffff', 'highlightColor': '#75e6db', 'strokeColor': '#08121e',
                    'strokeWidth': 1, 'shadow': True, 'fontBold': True, 'letterSpacing': 0,
                    'lineSpacing': 1.15, 'x': .5, 'y': .78, 'entranceId': 'none', 'exitId': 'none',
                    'fadeMs': 180, 'backgroundColor': '#141c36', 'bpm': 120, 'fps': 24}
DEFAULT_SETTINGS['layoutParameters'] = {}


def find_style(catalog, identifier):
    for row in catalog:
        if row['id'] == identifier:
            return row
    raise ValueError(f'不支持的样式目录 ID：{identifier}')


def normalize_settings(value):
    """Fill old settings defaults and reject invalid renderer parameters."""
    if not isinstance(value, dict):
        raise ValueError('样式设置必须是对象')
    result = dict(DEFAULT_SETTINGS)
    result.update(value)
    result['lyricSweep'] = value.get('lyricSweep', result['templateId'] in ('karaoke','karaoke-card'))
    if type(result['lyricSweep']) is not bool:
        raise ValueError('歌词走带设置必须是布尔值')
    for key, catalog in [('templateId', TEMPLATES), ('effectId', EFFECTS), ('layoutId', LAYOUTS),
                         ('entranceId', EFFECTS), ('exitId', EFFECTS)]:
        find_style(catalog, result[key])
    for key in ('textColor', 'highlightColor', 'strokeColor', 'backgroundColor'):
        if not isinstance(result[key], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', result[key]):
            raise ValueError(f'{key} 必须是 #RRGGBB 颜色')
    ranges = {'fontSize': (8, 200), 'strokeWidth': (0, 12), 'letterSpacing': (0, 20),
              'lineSpacing': (.8, 2), 'x': (0, 1), 'y': (0, 1), 'fadeMs': (0, 2000),
              'bpm': (40, 240), 'fps': (1, 60)}
    for key, (minimum, maximum) in ranges.items():
        number = result[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not minimum <= number <= maximum:
            raise ValueError(f'{key} 超出有效范围')
    for key in ('width', 'height'):
        if type(result[key]) is not int or not 16 <= result[key] <= 4096:
            raise ValueError('输出尺寸无效')
    if not isinstance(result['fontFamily'], str) or not result['fontFamily'].strip():
        raise ValueError('字体名不能为空')
    if type(result['fontBold']) is not bool or type(result['shadow']) is not bool:
        raise ValueError('字重和阴影设置必须是布尔值')
    parameter_ranges = {'offsetX': (-1, 1), 'offsetY': (-1, 1), 'amplitude': (0, .5),
                        'frequency': (0, 10), 'phase': (-100, 100), 'scale': (.25, 3), 'rotation': (-180, 180)}
    overrides = result['layoutParameters']
    if not isinstance(overrides, dict) or set(overrides) - set(parameter_ranges):
        raise ValueError('布局参数字段无效')
    for key, number in overrides.items():
        low, high = parameter_ranges[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not low <= number <= high:
            raise ValueError('布局参数超出有效范围：' + key)
    result['layoutParameters'] = dict(overrides)
    return result
