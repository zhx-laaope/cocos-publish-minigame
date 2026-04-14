# -*- coding: utf-8 -*-
"""
publish_tool.lib.utils
=====================
通用工具函数模块
"""

import os
import sys
import json
import shutil
import subprocess
import time
import datetime
import platform
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# ─────────────────── 控制台着色工具 ───────────────────

_ANSI = {
    'reset': '\033[0m',
    'bold': '\033[1m',
    'red': '\033[31m',
    'green': '\033[32m',
    'yellow': '\033[33m',
    'blue': '\033[34m',
    'magenta': '\033[35m',
    'cyan': '\033[36m',
}


def _c(text: str, color: str = None, bold: bool = False) -> str:
    prefix = ''
    if bold:
        prefix += _ANSI['bold']
    if color and color in _ANSI:
        prefix += _ANSI[color]
    suffix = _ANSI['reset']
    return f"{prefix}{text}{suffix}"


_LOG_START_TS = time.time()
_USE_COLOR = sys.stdout.isatty() and os.environ.get('NO_COLOR') is None


def _now_ts() -> str:
    now = datetime.datetime.now()
    return now.strftime('%H:%M:%S') + f'.{int(now.microsecond / 1000):03d}'


def _fmt_prefix(level: str) -> str:
    elapsed = time.time() - _LOG_START_TS
    return f"[{_now_ts()}][{level:<5}] +{elapsed:6.2f}s "


def _log(level: str, msg: str, *, color: str = None, bold: bool = False):
    msg = '' if msg is None else str(msg)
    prefix = _fmt_prefix(level)
    if '\n' in msg:
        msg = '\n'.join([msg.splitlines()[0]] + [(' ' * len(prefix)) + ln for ln in msg.splitlines()[1:]])
    line = prefix + msg
    if _USE_COLOR and color:
        line = _c(line, color=color, bold=bold)
    print(line, flush=True)


def log_info(msg: str):
    _log('INFO', msg, color='cyan')


def log_success(msg: str):
    _log('OK', msg, color='green', bold=True)


def log_warn(msg: str):
    _log('WARN', msg, color='yellow', bold=True)


def log_error(msg: str):
    _log('ERR', msg, color='red', bold=True)


def log_step(msg: str):
    _log('STEP', msg, color='magenta', bold=True)


# ─────────────────── 路径工具 ───────────────────

def resolve_path(path_value: str, base_dir: str = None) -> str:
    """解析路径，支持相对路径和绝对路径。"""
    if not path_value:
        return ''
    expanded = os.path.expanduser(path_value)
    if os.path.isabs(expanded):
        return os.path.abspath(expanded)
    base = base_dir or ''
    return os.path.abspath(os.path.join(base, expanded))


def resolve_tool_dir() -> str:
    """获取工具包根目录。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def detect_platform() -> str:
    """检测当前平台。"""
    system = platform.system().lower()
    if system == 'darwin':
        return 'darwin'
    elif system == 'windows' or os.name == 'nt':
        return 'win32'
    elif system == 'linux':
        return 'linux'
    return system


# ─────────────────── 配置加载 ───────────────────

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.json')


def load_config(config_path: str = None) -> Dict[str, Any]:
    """加载配置文件，支持默认值合并。"""
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH

    example_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'config.example.json')
    default_config = {}
    if os.path.exists(example_path):
        try:
            with open(example_path, 'r', encoding='utf-8') as f:
                default_config = json.load(f)
        except Exception:
            pass

    if not os.path.exists(config_path):
        return default_config

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            user_config = json.load(f)
        if isinstance(user_config, dict):
            return _deep_update(default_config, user_config)
    except Exception as exc:
        print(f"[config] 配置读取失败 {config_path}: {exc}")
    return default_config


def _deep_update(target: Dict, overrides: Dict) -> Dict:
    """深度合并字典。"""
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            target[key] = _deep_update(dict(target[key]), value)
        else:
            target[key] = value
    return target


# ─────────────────── PNGQuant 工具 ───────────────────

def get_pngquant_path(config: Dict[str, Any]) -> str:
    """获取当前平台对应的 pngquant 路径。"""
    pngquant_cfg = config.get('pngquant', {})
    binary_cfg = pngquant_cfg.get('binary', {})

    current_platform = detect_platform()
    if isinstance(binary_cfg, dict):
        binary_path = binary_cfg.get(current_platform) or binary_cfg.get('darwin', '')
    else:
        binary_path = binary_cfg

    if not binary_path:
        return ''

    tool_dir = resolve_tool_dir()
    full_path = os.path.join(tool_dir, binary_path)
    if os.path.exists(full_path):
        return full_path

    return resolve_path(binary_path)


# ─────────────────── 文件操作工具 ───────────────────

def copy_project_tree(src_root: str, dest_root: str, excludes: List[str] = None):
    """复制整个项目目录树，可选排除特定模式。"""
    excludes = [item for item in (excludes or []) if isinstance(item, str) and item]
    if os.path.exists(dest_root):
        shutil.rmtree(dest_root)
    os.makedirs(os.path.dirname(dest_root), exist_ok=True)

    use_rsync = shutil.which('rsync') is not None
    try:
        if use_rsync:
            os.makedirs(dest_root, exist_ok=True)
            cmd = ['rsync', '-a']
            for pattern in excludes:
                cmd.append(f'--exclude={pattern}')
            cmd.extend([os.path.join(src_root, ''), os.path.join(dest_root, '')])
            print(f"[build-copy] rsync {src_root} -> {dest_root}")
            subprocess.check_call(cmd)
        else:
            ignore = shutil.ignore_patterns(*excludes) if excludes else None
            print(f"[build-copy] copytree {src_root} -> {dest_root}")
            shutil.copytree(src_root, dest_root, ignore=ignore)
    except Exception as exc:
        if os.path.isdir(dest_root):
            shutil.rmtree(dest_root, ignore_errors=True)
        raise RuntimeError(f"[build-copy] 工程复制失败: {exc}")


def cleanup_build_workspace(path: str):
    """清理构建工作空间。"""
    if path and os.path.isdir(path):
        try:
            shutil.rmtree(path)
            print(f"[build-copy] 已删除临时工程: {path}")
        except Exception as exc:
            print(f"[build-copy] 删除临时工程失败 {path}: {exc}")


# ─────────────────── UUID 引用工具 ───────────────────

def extract_meta_uuids(target_path: str) -> List[Dict[str, str]]:
    """收集目标路径下所有 .meta 的 uuid 字段。"""
    meta_files = []
    if os.path.isfile(target_path):
        maybe_meta = f"{target_path}.meta" if not target_path.endswith('.meta') else target_path
        if os.path.exists(maybe_meta):
            meta_files.append(maybe_meta)
    else:
        for root, _, files in os.walk(target_path):
            for fn in files:
                if fn.endswith('.meta'):
                    meta_files.append(os.path.join(root, fn))

    uuids = []
    for meta_file in meta_files:
        try:
            with open(meta_file, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
                found = set()
                _collect_meta_uuids(data, found)
                for uid in sorted(found):
                    uuids.append({'uuid': uid, 'meta': meta_file})
        except Exception as exc:
            log_warn(f"[publish-ignore] 读取 meta 失败: {meta_file} ({exc})")
    return uuids


def _collect_meta_uuids(node, found: Set[str]):
    """递归收集 meta 中的 uuid 字段（含 subMetas）。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == 'uuid' and isinstance(value, str) and value:
                found.add(value)
            _collect_meta_uuids(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect_meta_uuids(item, found)


def find_uuid_references(project_root: str, uuids: List[Dict], exclude_path: str, file_exts: tuple = None) -> Dict[str, List[str]]:
    """使用 rg 搜索 uuid 引用，返回 {uuid: [files...]}。"""
    results = {}
    if not uuids:
        return results
    exclude_abs = os.path.abspath(exclude_path)
    allow_exts = tuple(file_exts or ())
    seen = {}

    for entry in uuids:
        uid = entry['uuid']
        try:
            proc = subprocess.run(
                ['rg', '-l', uid, project_root],
                check=False,
                capture_output=True,
                text=True
            )
            files = []
            if proc.stdout:
                for line in proc.stdout.splitlines():
                    abs_path = os.path.abspath(line.strip())
                    if abs_path.startswith(exclude_abs):
                        continue
                    if allow_exts and not abs_path.lower().endswith(allow_exts):
                        continue
                    key = (uid, abs_path)
                    if key in seen:
                        continue
                    seen[key] = True
                    files.append(abs_path)
            if files:
                results[uid] = files
        except FileNotFoundError:
            log_warn("[publish-ignore] 未检测到 ripgrep (rg)，跳过引用检查。")
            break
        except Exception as exc:
            log_warn(f"[publish-ignore] 引用检查异常 uuid={uid}: {exc}")
    return results


def replace_uuid_refs_in_json(node, target_uuids: Set[str]):
    """
    在 prefab/scene JSON 树中，将命中的 uuid 引用置空。
    返回 (new_node, changed_count)。
    """
    if isinstance(node, dict):
        uid = node.get('__uuid__')
        if isinstance(uid, str) and uid in target_uuids:
            return None, 1

        changed = 0
        for key, value in list(node.items()):
            new_value, inc = replace_uuid_refs_in_json(value, target_uuids)
            node[key] = new_value
            changed += inc
        return node, changed

    if isinstance(node, list):
        changed = 0
        for idx, item in enumerate(node):
            new_item, inc = replace_uuid_refs_in_json(item, target_uuids)
            node[idx] = new_item
            changed += inc
        return node, changed

    return node, 0


def disconnect_prefab_scene_references(refs: Dict[str, List[str]]) -> tuple:
    """
    将命中 uuid 的 prefab/scene 引用断开，返回 (patched, failed)。
    """
    file_to_uuids = {}
    for uid, files in refs.items():
        for f in files:
            file_to_uuids.setdefault(f, set()).add(uid)

    patched = {}
    failed = {}
    for asset_file, uid_set in file_to_uuids.items():
        try:
            with open(asset_file, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            data, changed = replace_uuid_refs_in_json(data, uid_set)
            if changed <= 0:
                continue
            with open(asset_file, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
                fh.write('\n')
            patched[asset_file] = changed
        except Exception as exc:
            failed[asset_file] = str(exc)

    return patched, failed


# ─────────────────── 忽略资源处理 ───────────────────

def load_publish_ignore_paths(raw_paths: List) -> List[str]:
    """规范化忽略路径列表。"""
    if not isinstance(raw_paths, list):
        return []

    valid_paths = []
    seen = set()
    for entry in raw_paths:
        if not isinstance(entry, str):
            continue
        norm = os.path.normpath(entry.strip())
        if norm and norm != '.' and norm not in seen:
            valid_paths.append(norm)
            seen.add(norm)
    return valid_paths


def resolve_ignore_stash_root(project_root: str, stash_raw: str = None) -> str:
    """解析忽略资源的暂存目录。"""
    if not stash_raw:
        return ''
    return resolve_path(stash_raw, base_dir=project_root)


def stash_publish_ignore_assets(project_root: str, rel_paths: List[str], stash_root: str = None) -> List[str]:
    """
    在复制体中删除忽略路径，删除前检查 uuid 引用。
    """
    removed = []
    if not rel_paths:
        return removed

    for rel_path in rel_paths:
        cleaned_rel = rel_path.lstrip('/\\')
        target_path = os.path.abspath(os.path.join(project_root, cleaned_rel))
        if not target_path.startswith(project_root):
            log_warn(f"[publish-ignore] 非法路径，已忽略: {target_path}")
            continue
        if not os.path.exists(target_path):
            log_info(f"[publish-ignore] 跳过不存在的路径: {target_path}")
            continue

        uuids = extract_meta_uuids(target_path)
        refs = find_uuid_references(
            project_root,
            uuids,
            target_path,
            file_exts=('.prefab', '.fire')
        )
        if refs:
            log_warn(f"[publish-ignore] 检测到 prefab/scene 引用，先断开关联再删除资源: {target_path}")
            for uid, files in refs.items():
                sample = '; '.join(files[:5])
                more = f" ...({len(files)})" if len(files) > 5 else ""
                log_warn(f"  uuid={uid} 引用于: {sample}{more}")

            patched, failed = disconnect_prefab_scene_references(refs)
            for file_path, count in patched.items():
                log_info(f"[publish-ignore] 已断开引用: {file_path} ({count} 处)")
            if failed:
                for file_path, err in failed.items():
                    log_error(f"[publish-ignore] 断开引用失败: {file_path} ({err})")
                log_error(f"[publish-ignore] 因存在断开失败，跳过删除目录: {target_path}")
                continue

        try:
            if os.path.isdir(target_path):
                shutil.rmtree(target_path)
            else:
                os.remove(target_path)
            removed.append(target_path)
            log_info(f"[publish-ignore] 已删除(复制体): {target_path}")
        except Exception as exc:
            log_error(f"[publish-ignore] 删除失败 {target_path}: {exc}")

    return removed


def restore_publish_ignore_assets(project_root: str, moved: List[str], stash_root: str = None):
    """
    复制体已删除忽略资源，无需恢复（保持兼容函数签名）。
    """
    if moved:
        log_info("[publish-ignore] 复制体已删除忽略资源，无需恢复。")


def restore_meta_with_git(project_root: str, rel_paths: List[str]):
    """
    使用 git 恢复指定相对路径下所有 .meta 文件到当前 HEAD 状态。
    """
    if not rel_paths:
        return
    git_dir = os.path.join(project_root, '.git')
    if not os.path.isdir(git_dir):
        print('[git-meta-restore] 未检测到 .git，跳过 git 恢复 .meta 操作')
        return

    meta_files = []
    for rel in rel_paths:
        cleaned_rel = rel.lstrip('/\\')
        abs_path = os.path.abspath(os.path.join(project_root, cleaned_rel))
        if not os.path.exists(abs_path):
            print(f"[git-meta-restore] 跳过不存在路径: {abs_path}")
            continue
        for root, _, files in os.walk(abs_path):
            for fn in files:
                if fn.endswith('.meta'):
                    meta_files.append(os.path.relpath(os.path.join(root, fn), project_root))

    if not meta_files:
        print('[git-meta-restore] 未找到任何 .meta 文件需要恢复')
        return

    batch_size = 200
    for i in range(0, len(meta_files), batch_size):
        batch = meta_files[i:i + batch_size]
        cmd = ['git', 'checkout', 'HEAD', '--'] + batch
        try:
            print(f"[git-meta-restore] 恢复 .meta，执行: git checkout HEAD -- <{len(batch)} files>")
            subprocess.check_call(cmd, cwd=project_root)
        except subprocess.CalledProcessError as exc:
            print(f"[git-meta-restore] git 恢复失败: {exc}")


# ─────────────────── 微信项目配置工具 ───────────────────

def update_wechatgame_project_config(proj_root_dir: str, project_name: str, **kwargs):
    """
    统一修改 wechatgame/project.config.json 的配置项。
    """
    config_path = os.path.join(proj_root_dir, project_name, "project.config.json")
    if not os.path.exists(config_path):
        print(f"未找到 {config_path}，跳过配置修改")
        return

    if not kwargs:
        print("未提供任何配置项，跳过修改")
        return

    SETTING_FIELDS = {
        'urlCheck', 'es6', 'minified', 'compileHotReLoad', 'postcss',
        'minifyWXML', 'minifyWXSS', 'uglifyFileName', 'uploadWithSourceMap',
        'enhance', 'coverView', 'ignoreDevUnusedFiles', 'checkInvalidKey',
        'showShadowRootInWxmlPanel', 'packNpmManually', 'packNpmRelationList',
        'babelSetting', 'condition', 'swc', 'disableSWC'
    }

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        modified = []
        setting = config.get("setting") or {}

        for key, value in kwargs.items():
            if key.startswith("setting."):
                actual_key = key[len("setting."):]
                old_val = setting.get(actual_key)
                if old_val != value:
                    setting[actual_key] = value
                    modified.append(f"setting.{actual_key}: {old_val} -> {value}")
            elif key in SETTING_FIELDS:
                old_val = setting.get(key)
                if old_val != value:
                    setting[key] = value
                    modified.append(f"setting.{key}: {old_val} -> {value}")
            else:
                old_val = config.get(key)
                if old_val != value:
                    config[key] = value
                    modified.append(f"{key}: {old_val} -> {value}")

        if setting:
            config["setting"] = setting

        if modified:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            print(f"已修改 {config_path}:")
            for m in modified:
                print(f"  - {m}")
        else:
            print(f"{config_path} 配置无需修改（值已相同）")

    except Exception as e:
        print(f"修改 {config_path} 失败: {e}")


# ─────────────────── 时间格式化工具 ───────────────────

def format_time(all_time: float) -> str:
    """格式化时间显示。"""
    if all_time < 60:
        return f"{int(all_time)} 秒"
    mins = divmod(all_time, 60)
    return f"{int(mins[0])} 分, {int(mins[1])} 秒"
