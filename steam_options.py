"""UI-only Steam launch-option ownership. No Steam configuration-file access."""
import hashlib
import json
from installer_core import SafetyError, atomic_json, launch_option


MANUAL = ('自动操作未完成。请保持 Steam 运行，手动打开 战场兄弟 → 属性 → 常规 → 启动选项。'
          '设置前自行保存原值；卸载时恢复原值（原值为空则清空），不要盲目拼接或清空其他选项。')
FIELDS = {'schema', 'appid', 'game', 'original', 'original_sha256', 'applied', 'applied_sha256', 'phase'}


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def valid_text(value):
    return isinstance(value, str) and len(value) <= 32768 and not any(c in value for c in '\x00\r\n')


def load(engine):
    path = engine.store / 'launch-options.json'
    if not path.exists():
        return None
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate key')
                result[key] = value
            return result
        if path.stat().st_size > 300000:
            raise ValueError('oversized state')
        value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)
        if (not isinstance(value, dict) or set(value) != FIELDS or type(value['schema']) is not int
                or value['schema'] != 1 or value['appid'] != '365360' or value['game'] != str(engine.game)
                or value['phase'] not in ('pending', 'applied', 'restored')):
            raise ValueError('schema')
        for field in ('original', 'applied'):
            if not valid_text(value[field]) or value[field + '_sha256'] != digest(value[field]):
                raise ValueError('value/hash')
        if value['applied'] != launch_option(engine.game):
            raise ValueError('foreign applied option')
        return value
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise SafetyError('Steam 启动选项备份不可信；拒绝自动修改。' + MANUAL) from exc


def backend_or_default(backend):
    if backend is None:
        from steam_ui import AutoSteamUI
        return AutoSteamUI()
    return backend


def set_options(engine, backend=None):
    backend = backend_or_default(backend)
    state = load(engine)
    try:
        current = backend.read()
    except Exception as exc:
        raise SafetyError('未能自动定位，未写入。无法验证 Steam 属性页及启动选项输入框。' + MANUAL) from exc
    if not valid_text(current):
        raise SafetyError('无法安全读取 Steam 启动选项。' + MANUAL)
    if state and state['phase'] != 'restored':
        if current == state['applied'] and state['phase'] == 'applied':
            return {'status': 'applied', 'note': '已核验启动选项；保留首次原值备份。'}
        if current not in (state['original'], state['applied']):
            raise SafetyError('Steam 启动选项已被用户修改；保留原状及备份。' + MANUAL)
    else:
        if current == launch_option(engine.game):
            raise SafetyError('现有选项已是汉化启动命令，但没有可用的原值备份；拒绝猜测原值。' + MANUAL)
        state = {'schema': 1, 'appid': '365360', 'game': str(engine.game), 'original': current,
                 'original_sha256': digest(current), 'applied': launch_option(engine.game),
                 'applied_sha256': digest(launch_option(engine.game)), 'phase': 'pending'}
    state['phase'] = 'pending'
    atomic_json(engine.store / 'launch-options.json', state)  # Durable before the first UI write.
    try:
        backend.exchange(current, state['applied'])
        if backend.read() != state['applied']:
            raise SafetyError('Steam 设置后读回不匹配')
        state['phase'] = 'applied'
        atomic_json(engine.store / 'launch-options.json', state)
    except Exception as exc:
        # Never clobber a third value. Leave the durable record for manual/retry recovery.
        try:
            observed = backend.read()
            if observed == state['applied']:
                backend.exchange(state['applied'], state['original'])
            if backend.read() != state['original']:
                raise SafetyError('回滚未确认；备份已保留')
        except Exception as rollback:
            raise SafetyError('自动设置失败，回滚未确认；请使用保留的原值备份。' + MANUAL) from rollback
        raise SafetyError('自动设置失败，已读回确认原值；可重试。' + MANUAL) from exc
    return {'status': 'applied', 'note': '已通过 Steam 界面设置并重新打开读回确认；原值已备份。'}


def restore_options(engine, backend=None):
    state = load(engine)
    if not state or state['phase'] == 'restored':
        return {'status': 'not-managed', 'note': '无待恢复的自动设置记录；手动设置的选项须自行核对。'}
    backend = backend_or_default(backend)
    try:
        current = backend.read()
    except Exception as exc:
        raise SafetyError('未能自动定位，未写入。无法验证 Steam 属性页及启动选项输入框。' + MANUAL) from exc
    if current == state['original']:
        state['phase'] = 'restored'
        atomic_json(engine.store / 'launch-options.json', state)
        return {'status': 'restored', 'note': '已读回确认原始 Steam 启动选项。'}
    if current != state['applied']:
        raise SafetyError('Steam 启动选项已被用户修改；拒绝恢复，未更改选项或卸载文件。' + MANUAL)
    try:
        backend.exchange(state['applied'], state['original'])
        if backend.read() != state['original']:
            raise SafetyError('Steam 恢复后读回不匹配')
    except Exception as exc:
        raise SafetyError('Steam 原值恢复未确认；未卸载文件，备份保留以便重试。' + MANUAL) from exc
    state['phase'] = 'restored'
    atomic_json(engine.store / 'launch-options.json', state)
    return {'status': 'restored', 'note': '已恢复并重新打开读回确认原始 Steam 启动选项。'}
