"""Local subtitle preset data; no external app, font or image is needed at runtime."""
import copy
import json
from functools import lru_cache
from pathlib import Path

DECORATION_MODES = (
    'noir', 'crimson', 'caution', 'magenta', 'paper', 'hud', 'mint', 'specimen',
    'transit', 'blueprint', 'rouge', 'mono', 'hrRuin', 'hrNightRec', 'hrCurse',
    'sakura', 'ocean', 'sunset', 'forest', 'vapor', 'newsprint', 'synth80',
    'kraft', 'candy', 'acid', 'sumi', 'gold',
)
CATALOG_PATH = Path(__file__).resolve().parents[1] / 'assets' / 'style_presets.json'


@lru_cache(maxsize=1)
def _catalog():
    data = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    styles = data['styles']
    if (data.get('schema') != 'baofang-subtitle-presets/v1'
            or data.get('count') != 27 or len(styles) != 27
            or data.get('schemeCount') != 88
            or sum(len(item['schemes']) for item in styles) != 88
            or tuple(item['decorationMode'] for item in styles) != DECORATION_MODES
            or any(item['id'] != 'jizura-' + item['decorationMode'] for item in styles)):
        raise ValueError('本地字幕预设目录无效')
    return {item['id']: item for item in styles}


def preset_definition(preset_id):
    """Return an isolated copy so previews cannot modify shared catalog colors."""
    if not isinstance(preset_id, str) or preset_id not in _catalog():
        raise ValueError('字幕预设无效')
    return copy.deepcopy(_catalog()[preset_id])


def preset_scheme_count(preset_id):
    if preset_id == 'custom' or preset_id in ('classic', 'gold', 'neon', 'minimal'):
        return 1
    return len(preset_definition(preset_id)['schemes'])


def style_catalog():
    """QML-ready names, palettes and complete actual renderer preferences."""
    # Kept lazy: style validation reads the palette catalog in the other direction.
    from app.styles import style_preset
    result = []
    for preset_id, source in _catalog().items():
        item = copy.deepcopy(source)
        for scheme in item['schemes']:
            scheme['colors'] = {key: value for key, value in scheme.items()
                                if key not in ('name', 'paper', 'swap')}
        item['previewStyle'] = style_preset(preset_id)
        result.append(item)
    return result
