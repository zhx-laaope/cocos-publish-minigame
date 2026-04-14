# -*- coding: utf-8 -*-
"""
publish_tool.lib.split_scripts
===========================
Cocos Creator 2.x 微信小游戏构建后处理脚本：拆分 start-scene/index.js

问题：Cocos Creator 2.x 将所有 TypeScript/JS 脚本打包到 start-scene bundle 的
index.js 中（约 2MB），导致首包过大、冷启动慢。

方案：构建完成后将 index.js 中的模块拆分为：
  - start-scene/index.js（最小骨架）：仅保留 browserify 运行时 + 数字 key 的内部模块
  - subpackages/logic/game.js（全部业务模块）：所有命名模块

用法：
    from publish_tool.lib import split_start_scene_bundle
    split_start_scene_bundle(wechatgame_dir)
"""

import re
import os
import json
from collections import deque
from typing import Optional, Dict, Set, List


# ─────────────────── 配置 ───────────────────

LOGIC_SUBPACKAGE_NAME = 'logic'
LOGIC_SUBPACKAGE_ROOT = 'subpackages/logic'

_MERGE_SNIPPET = (
    'var _d=window.__deferred_modules;'
    'if(_d){for(var _k in _d)t[_k]=_d[_k];'
    'delete window.__deferred_modules;}'
)

_GAME_JS_INJECT = '''\
        // [split-scripts] Load deferred modules subpackage before boot
        if (!loadGame.__ld) {
                loadGame.__ld = true;
                var _logicOk = false;
                try { require('subpackages/logic/game.js'); _logicOk = true; } catch(_e) {}
                if (!_logicOk && typeof wx !== 'undefined' && wx.loadSubpackage) {
                        var _logicDone = false;
                        var _logicTimer = setTimeout(function() {
                                if (!_logicDone) {
                                        _logicDone = true;
                                        console.error('[split-scripts] logic subpackage load timeout(10s), proceeding without it');
                                        loadGame();
                                }
                        }, 10000);
                        wx.loadSubpackage({
                                name: 'logic',
                                success: function() {
                                        if (_logicDone) return;
                                        _logicDone = true;
                                        clearTimeout(_logicTimer);
                                        try { require('subpackages/logic/game.js'); } catch(_e) {
                                                console.error('[split-scripts] logic require failed after download:', _e);
                                        }
                                        loadGame();
                                },
                                fail: function(_e) {
                                        if (_logicDone) return;
                                        _logicDone = true;
                                        clearTimeout(_logicTimer);
                                        console.error('[split-scripts] logic subpackage load failed', _e);
                                        loadGame();
                                }
                        });
                        return;
                }
        }
'''


# ─────────────────── 解析 ───────────────────

def _parse_bundle_structure(content: str):
    """
    解析 browserify 产物的三段结构。

    返回 (wrapper, modules_start, modules_end, entries_tail)
    """
    wrapper_match = re.search(r'\}\(\{', content)
    if not wrapper_match:
        raise ValueError('无法找到 browserify wrapper/modules 边界 }({')

    wrapper = content[:wrapper_match.end()]
    modules_start = wrapper_match.end() - 1

    sep_pos = content.rfind('},{},')
    if sep_pos < 0:
        raise ValueError('无法找到 modules/entries 分隔符 },{},[')

    modules_end = sep_pos
    entries_tail = content[modules_end + 1:]

    return wrapper, modules_start, modules_end, entries_tail


def _find_module_boundaries(content: str, modules_start: int, modules_end: int) -> List[Dict]:
    """在 modules 对象区域内定位每个模块的边界。"""
    region = content[modules_start:modules_end + 1]

    pattern = re.compile(
        r'(?:^\{|,)'
        r'(\d+|[A-Za-z_][\w.]*)'
        r':\[function\('
    )

    matches = list(pattern.finditer(region))
    if not matches:
        raise ValueError('modules 区域中未找到任何模块定义')

    modules = []
    for i, m in enumerate(matches):
        key = m.group(1)
        prefix_char = m.group(0)[0]
        if prefix_char in ('{', ','):
            content_start = m.start() + 1
        else:
            content_start = m.start()

        if i < len(matches) - 1:
            content_end = matches[i + 1].start()
        else:
            content_end = len(region) - 1

        modules.append({
            'key': key,
            'start': modules_start + content_start,
            'end': modules_start + content_end,
        })

    return modules


def _extract_dep_values(content: str, mod: Dict) -> List[str]:
    """从单个模块定义的文本中提取依赖映射的目标模块名。"""
    mod_text = content[mod['start']:mod['end']]
    dep_match = re.search(r',\{([^}]*)\}\]\s*$', mod_text)
    if not dep_match:
        return []
    dep_str = dep_match.group(1)
    pairs = re.findall(r'"[^"]+"\s*:\s*"([^"]*)"', dep_str)
    return pairs


def _build_dep_graph(content: str, module_bounds: List[Dict]) -> Dict[str, List[str]]:
    """构建模块依赖图。"""
    all_keys = {m['key'] for m in module_bounds}
    dep_graph = {}
    for mod in module_bounds:
        dep_values = _extract_dep_values(content, mod)
        dep_graph[mod['key']] = [d for d in dep_values if d in all_keys]
    return dep_graph


def _bfs_transitive_deps(seeds: Set[str], dep_graph: Dict[str, List[str]]) -> Set[str]:
    """从种子模块出发，BFS 收集所有传递依赖。"""
    visited = set()
    queue = deque()
    for seed in seeds:
        if seed in dep_graph:
            queue.append(seed)
            visited.add(seed)
    while queue:
        current = queue.popleft()
        for dep in dep_graph.get(current, []):
            if dep not in visited:
                visited.add(dep)
                queue.append(dep)
    return visited


# ─────────────────── 注入 / 修改 ───────────────────

def _inject_merge_into_wrapper(wrapper: str) -> str:
    """在 wrapper 函数体开头注入 deferred modules 合并代码。"""
    m = re.search(r'function\s+\w*\((\w+),\w+,\w+\)\{', wrapper)
    if not m:
        raise ValueError('无法在 wrapper 中定位 function(t,i,a){ 模式')

    modules_param = m.group(1)
    merge_code = (
        'var _d=window.__deferred_modules;'
        'if(_d){for(var _k in _d)' + modules_param + '[_k]=_d[_k];'
        'delete window.__deferred_modules;}'
    )

    inject_pos = m.end()
    return wrapper[:inject_pos] + merge_code + wrapper[inject_pos:]


def _modify_game_js(wechatgame_dir: str) -> bool:
    """修改 game.js：注入子包加载逻辑。"""
    game_js_path = os.path.join(wechatgame_dir, 'game.js')
    if not os.path.exists(game_js_path):
        print('[split-scripts] 未找到 game.js，跳过修改')
        return False

    with open(game_js_path, 'r', encoding='utf-8') as f:
        content = f.read()

    if 'split-scripts' in content:
        print('[split-scripts] game.js 已包含注入代码，跳过')
        return True

    anchor = 'function loadGame() {\n'
    anchor_pos = content.find(anchor)
    if anchor_pos < 0:
        anchor = 'function loadGame() {'
        anchor_pos = content.find(anchor)

    if anchor_pos < 0:
        print('[split-scripts] 警告: 无法在 game.js 中定位 loadGame 函数，跳过修改')
        return False

    inject_pos = anchor_pos + len(anchor)
    new_content = content[:inject_pos] + '\n' + _GAME_JS_INJECT + content[inject_pos:]

    with open(game_js_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    return True


def _update_game_json(wechatgame_dir: str) -> bool:
    """在 game.json 中注册 logic 子包 + 并行预加载。"""
    game_json_path = os.path.join(wechatgame_dir, 'game.json')
    if not os.path.exists(game_json_path):
        print('[split-scripts] 未找到 game.json，跳过修改')
        return False

    with open(game_json_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    changed = False

    subpackages = config.setdefault('subpackages', [])
    if not any(sp.get('name') == LOGIC_SUBPACKAGE_NAME for sp in subpackages):
        subpackages.append({
            'name': LOGIC_SUBPACKAGE_NAME,
            'root': LOGIC_SUBPACKAGE_ROOT
        })
        changed = True

    parallel = config.setdefault('parallelPreloadSubpackages', [])
    if not any(sp.get('name') == LOGIC_SUBPACKAGE_NAME for sp in parallel):
        parallel.insert(0, {'name': LOGIC_SUBPACKAGE_NAME})
        changed = True

    if changed:
        with open(game_json_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent='\t')
            f.write('\n')

    return changed


# ─────────────────── 主入口 ───────────────────

def split_start_scene_bundle(
    wechatgame_dir: str,
    extra_core_modules: Optional[Set[str]] = None,
    dry_run: bool = False
) -> Optional[Dict]:
    """
    拆分 start-scene/index.js 为核心模块 + 子包业务模块。

    Args:
        wechatgame_dir: 微信小游戏构建产物根目录
        extra_core_modules: 额外需要保留在核心中的模块名集合
        dry_run: 为 True 时只分析不修改文件

    Returns:
        拆分统计信息，失败返回 None
    """
    index_js_path = os.path.join(wechatgame_dir, 'assets', 'start-scene', 'index.js')
    if not os.path.exists(index_js_path):
        print(f'[split-scripts] 未找到 {index_js_path}，跳过拆分')
        return None

    with open(index_js_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original_size = len(content.encode('utf-8'))
    print(f'[split-scripts] 原始 index.js 大小: {original_size:,} bytes')

    try:
        wrapper, modules_start, modules_end, entries_tail = _parse_bundle_structure(content)
    except ValueError as e:
        print(f'[split-scripts] 解析失败: {e}')
        return None

    try:
        module_bounds = _find_module_boundaries(content, modules_start, modules_end)
    except ValueError as e:
        print(f'[split-scripts] 模块定位失败: {e}')
        return None

    print(f'[split-scripts] 共发现 {len(module_bounds)} 个模块')

    # 核心模块策略：仅保留数字 key 的内部模块
    core_keys = set()
    for mod in module_bounds:
        if mod['key'].isdigit():
            core_keys.add(mod['key'])

    if extra_core_modules:
        all_keys = {m['key'] for m in module_bounds}
        for key in extra_core_modules:
            if key in all_keys:
                core_keys.add(key)

    core_mods = [m for m in module_bounds if m['key'] in core_keys]
    deferred_mods = [m for m in module_bounds if m['key'] not in core_keys]

    if not deferred_mods:
        print('[split-scripts] 所有模块均在核心依赖中，无需拆分')
        return None

    core_text_size = sum(m['end'] - m['start'] for m in core_mods)
    deferred_text_size = sum(m['end'] - m['start'] for m in deferred_mods)

    print(f'[split-scripts] 核心模块: {len(core_mods)} 个 (~{core_text_size:,} bytes)')
    print(f'[split-scripts] 延迟模块: {len(deferred_mods)} 个 (~{deferred_text_size:,} bytes)')
    print(f'[split-scripts] 预计首包缩减: ~{deferred_text_size * 100 // original_size}%')

    if dry_run:
        print('[split-scripts] dry_run 模式，不修改文件')
        print('[split-scripts] 核心模块列表:')
        for m in sorted(core_mods, key=lambda x: x['key']):
            print(f'  - {m["key"]}')
        return {
            'original_size': original_size,
            'total_modules': len(module_bounds),
            'core_modules': len(core_mods),
            'deferred_modules': len(deferred_mods),
            'estimated_core_size': core_text_size,
            'estimated_deferred_size': deferred_text_size,
        }

    # 组装核心和延迟模块文本
    core_parts = [content[m['start']:m['end']] for m in core_mods]
    core_modules_str = '{' + ','.join(core_parts) + '}'

    deferred_parts = [content[m['start']:m['end']] for m in deferred_mods]
    deferred_modules_str = '{' + ','.join(deferred_parts) + '}'

    try:
        modified_wrapper = _inject_merge_into_wrapper(wrapper)
    except ValueError as e:
        print(f'[split-scripts] wrapper 注入失败: {e}')
        return None

    new_index = modified_wrapper[:-1] + core_modules_str + entries_tail
    subpackage_script = 'window.__deferred_modules=' + deferred_modules_str + ';\n'

    # 备份并写入
    backup_path = index_js_path + '.bak'
    if not os.path.exists(backup_path):
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(content)

    with open(index_js_path, 'w', encoding='utf-8') as f:
        f.write(new_index)

    logic_dir = os.path.join(wechatgame_dir, LOGIC_SUBPACKAGE_ROOT)
    os.makedirs(logic_dir, exist_ok=True)
    logic_game_js = os.path.join(logic_dir, 'game.js')
    with open(logic_game_js, 'w', encoding='utf-8') as f:
        f.write(subpackage_script)

    new_index_size = len(new_index.encode('utf-8'))
    logic_size = len(subpackage_script.encode('utf-8'))

    print(f'[split-scripts] 新 start-scene/index.js: {new_index_size:,} bytes')
    print(f'[split-scripts] 新 subpackages/logic/game.js: {logic_size:,} bytes')

    _update_game_json(wechatgame_dir)
    print('[split-scripts] game.json 已更新（logic 子包 + 并行预加载）')

    if _modify_game_js(wechatgame_dir):
        print('[split-scripts] game.js 已注入子包加载逻辑')
    else:
        print('[split-scripts] 警告: game.js 修改失败，需手动处理')

    reduction = original_size - new_index_size
    print(f'[split-scripts] 拆分完成! 首包缩减 {reduction:,} bytes ({reduction * 100 // original_size}%)')

    return {
        'original_size': original_size,
        'core_size': new_index_size,
        'deferred_size': logic_size,
        'total_modules': len(module_bounds),
        'core_modules': len(core_mods),
        'deferred_modules': len(deferred_mods),
        'reduction_bytes': reduction,
        'reduction_percent': reduction * 100 // original_size,
    }
