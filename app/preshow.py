"""开场自检：把现场状态翻成一张短清单。

只读本机已有状态，不连网、不上传、不改设置。每项只说能核实的事：
客屏窗口打开不等于场地电视有画面；读到打碟机位置不等于人声来源已核验。
"""
from urllib.parse import urlsplit

from app.guest_inbox import _private

GREEN, AMBER, RED, MUTED = 'green', 'amber', 'red', 'muted'
DJ_NAMES = {'serato': 'Serato', 'rekordbox': 'rekordbox 测试副本', 'rekordbox_ax': 'rekordbox 原版'}
FOLLOW = {'auto': '自动选盘', 'A': '固定 A 盘', 'B': '固定 B 盘'}


def _row(key, label, tone, detail, action='', action_label=''):
    return {'id': key, 'label': label, 'tone': tone, 'detail': detail,
            'action': action, 'actionLabel': action_label}


def _short(text, limit=120):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _count(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def dj_row(state):
    name = DJ_NAMES.get(state.get('mode'))
    if not name:
        return _row('dj', '打碟机', AMBER, '回放演示，没接真打碟机。在现场页左上角选 Serato 或 rekordbox。')
    if state.get('bridgeRunning') is False:
        return _row('dj', '打碟机', RED, name + ' 跟随已停止。', 'connect', '重新连接')
    if not state.get('readerConnected'):
        status = _short(state.get('bridgeStatus'))
        return _row('dj', '打碟机', RED, name + ' 还没连上' + ('：' + status if status else '。'), 'connect', '连接')
    if state.get('stale'):
        return _row('dj', '打碟机', AMBER, '已连上 ' + name + '，还没读到播放位置。在打碟机上放一首歌试试。')
    return _row('dj', '打碟机', GREEN, '已读到 ' + name + ' 的播放位置。')


def output_row(state, screens):
    screens = [row for row in screens or () if isinstance(row, dict)]
    target = state.get('outputTarget') or {}
    wanted = target.get('screenName') or ''
    single = len(screens) < 2
    recovery = state.get('outputRecovery') or {}
    if recovery.get('phase') == 'waiting':
        return _row('output', '客屏', AMBER, _short(recovery.get('detail')))
    if recovery.get('phase') == 'failed':
        return _row('output', '客屏', RED, _short(recovery.get('detail')), 'retryOutput', '重新打开')
    if not state.get('outputOpen'):
        return _row('output', '客屏', AMBER, '还没打开。' + ('现在只接了一块屏，打开后会盖住控制台。' if single else ''),
                    'openOutput', '打开客屏')
    if state.get('outputDestination') == 'serato_overlay':
        overlay = state.get('overlayStatus') or {}
        attached = overlay.get('phase') == 'attached'
        return _row('output', '客屏', GREEN if attached else AMBER,
                    '字幕叠加到 Serato：' + (_short(overlay.get('detail'), 80) or '正在找视频输出窗口。'))
    match = next((row for row in screens if row.get('name') == wanted), None) if wanted else None
    if wanted and not match:
        return _row('output', '客屏', RED, '选的显示器断开了，客屏退回普通窗口。', 'screens', '选显示器')
    if single:
        return _row('output', '客屏', AMBER, '已打开，但只接了一块屏。接上电视后选显示器。', 'screens', '选显示器')
    if not match:
        return _row('output', '客屏', AMBER, '已打开，还没选显示器。', 'screens', '选显示器')
    mode = '全屏' if target.get('fullScreen') else '窗口'
    return _row('output', '客屏', GREEN, '已在' + _short(match.get('label') or wanted, 60) + '，' + mode + '显示。')


def lyrics_row(state):
    selected = state.get('selectedSource')
    if selected == 'none':
        return _row('lyrics', '歌词显示', AMBER, '歌词现在是隐藏的，客人看不到。', 'restore', '恢复歌词')
    if state.get('effectiveSource') not in ('A', 'B'):
        selection = state.get('selection')
        selection = selection if isinstance(selection, dict) else {}
        reason = _short(selection.get('reason') or state.get('selectionReason') or '当前歌词来源尚未就绪', 80)
        advice = '请在现场右侧指定 A / B 盘。' if selected == 'auto' else '请在现场检查所选盘和歌词。'
        return _row('lyrics', '歌词显示', AMBER, reason + '；' + advice, 'routing', '查看来源')
    return _row('lyrics', '歌词显示', GREEN, '没有隐藏，' + FOLLOW.get(selected, '自动选盘') + '。')


def deck_row(state, side):
    key, label = 'deck' + side, side + ' 盘'
    if state.get('mode') not in DJ_NAMES:
        return _row(key, label, MUTED, '回放样例，不是真歌。')
    deck = state.get('deck' + side) or {}
    cache = deck.get('cacheState')
    title = _short(deck.get('title'), 40)
    song = '《' + title + '》' if title else '这首歌'
    note = _short(deck.get('cacheLabel'), 80)
    if cache == 'ready':
        if deck.get('needsReview'):
            return _row(key, label, AMBER, song + '有歌词，待复核。')
        return _row(key, label, GREEN, song + '歌词就绪。')
    if cache == 'missing':
        return _row(key, label, AMBER, song + '没有歌词，上屏会空着。', 'prepare', '去准备')
    if cache == 'preparing':
        return _row(key, label, AMBER, song + '：' + (note or '正在核验歌曲文件。'))
    if cache == 'error':
        return _row(key, label, RED, song + '：' + (note or '文件核验失败。'))
    return _row(key, label, MUTED, '没读到这盘在放什么。')


def setlist_row(state):
    setlist = state.get('setlist') or {}
    total = _count(setlist.get('total'))
    if not total:
        return _row('setlist', '今晚歌单', MUTED, '还没排歌单，不排也能演。', 'library', '去排歌')
    counts = setlist.get('counts') or {}
    ready, review, missing, gone = (_count(counts.get(name)) for name in ('ready', 'review', 'missing', 'gone'))
    parts = [f'{total} 首', f'就绪 {ready}']
    parts += [f'{label} {value}' for label, value in (('待复核', review), ('没歌词', missing), ('找不到', gone)) if value]
    if missing or gone:
        return _row('setlist', '今晚歌单', AMBER, ' · '.join(parts), 'library', '去曲库')
    return _row('setlist', '今晚歌单', GREEN, ' · '.join(parts))


def midi_row(state):
    midi = state.get('midi') or {}
    mapped = sum(1 for row in midi.get('rows') or () if isinstance(row, dict) and row.get('key'))
    port = _short(midi.get('port'), 40)
    if not port and not mapped:
        return _row('midi', 'MIDI', MUTED, '没用 MIDI 控制器。')
    if midi.get('available') is False:
        return _row('midi', 'MIDI', RED, 'MIDI 组件没装好，按键不起作用。')
    if port and midi.get('connected'):
        if mapped:
            return _row('midi', 'MIDI', GREEN, f'已连上「{port}」，设了 {mapped} 个键。')
        return _row('midi', 'MIDI', AMBER, f'已连上「{port}」，还没设键。', 'midi', '设键')
    if port:
        return _row('midi', 'MIDI', RED, f'「{port}」没连上，按键不起作用。', 'midi', 'MIDI 设置')
    return _row('midi', 'MIDI', AMBER, f'设了 {mapped} 个键，但没选设备。', 'midi', 'MIDI 设置')


def wish_row(state, lan_ip):
    qr = state.get('guestQr') or {}
    relay_status = state.get('guestRelayStatus') or ''
    if qr.get('mode') == 'public':
        return _row('wish', '扫码祝福', GREEN if qr.get('running') else AMBER,
                    relay_status, 'wish', '检查扫码祝福')
    if relay_status and not qr.get('running'):
        return _row('wish', '扫码祝福', MUTED if (state.get('offlineLibrary') or {}).get('offlineOnly') else AMBER,
                    relay_status, 'wish', '检查扫码祝福')
    lan_ip = str(lan_ip or '')
    if qr.get('running'):
        # A running server may still advertise the loopback URL generated
        # before Wi-Fi connected. Check the stored QR, without probing phones.
        try:
            url = urlsplit(str(qr.get('url') or ''))
            host = url.hostname if url.scheme == 'http' else None
        except ValueError:
            host = None
        if not _private(lan_ip):
            detail = '服务已开，但这台电脑没连局域网，手机扫不到。连网后请关闭再打开扫码祝福。'
        elif not host or not _private(host):
            detail = '服务已开，但二维码没有可用的局域网地址，手机扫不到。请关闭再打开扫码祝福。'
        elif host != lan_ip:
            detail = '网络地址已变化，二维码仍用旧地址。请关闭再打开扫码祝福。'
        else:
            detail = ''
        if detail:
            return _row('wish', '扫码祝福', AMBER, detail, 'wish', '检查扫码祝福')
        where = '二维码在客屏角上' if qr.get('onScreen') else '二维码没放到客屏'
        return _row('wish', '扫码祝福', GREEN, '已打开，' + where + '。客人发来的要你点「上屏」才显示。')
    if not _private(lan_ip):
        return _row('wish', '扫码祝福', MUTED, '关着。这台电脑没连局域网，开了手机也扫不到。')
    return _row('wish', '扫码祝福', MUTED, '关着，要用时在现场页打开。局域网地址 ' + _short(lan_ip, 40) + '。')


def cloud_row(state):
    if (state.get('offlineLibrary') or {}).get('offlineOnly'):
        return _row('cloud', '离线演出', GREEN, '已开。只用本机歌词，不发云请求。')
    ready = bool(((state.get('preparation') or {}).get('config') or {}).get('ready'))
    key = '百炼 Key 已填' if ready else '没填百炼 Key'
    return _row('cloud', '离线演出', MUTED, '没开，云请求还能发（' + key + '）。演出时可以打开。', 'offline', '打开离线演出')


def report(state, screens=(), lan_ip=''):
    state = state or {}
    rows = [dj_row(state), output_row(state, screens), lyrics_row(state), deck_row(state, 'A'), deck_row(state, 'B'),
            setlist_row(state), midi_row(state), wish_row(state, lan_ip), cloud_row(state)]
    broken = sum(row['tone'] == RED for row in rows)
    todo = sum(row['tone'] == AMBER for row in rows)
    if broken:
        summary = f'{broken} 项有问题，先处理红点。' + (f'另有 {todo} 项要看一下。' if todo else '')
    elif todo:
        summary = f'{todo} 项要看一下，其余正常。'
    else:
        summary = '能查的都正常。'
    return {'rows': rows, 'summary': summary, 'broken': broken, 'todo': todo}
