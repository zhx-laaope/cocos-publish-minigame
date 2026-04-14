# -*- coding: utf-8 -*-
"""
publish_tool.lib.texture
====================
纹理压缩格式管理模块
"""

import os
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 纹理格式相关常量
SUPPORTED_META_SUFFIXES = (".png.meta", ".pac.meta")
SOURCE_TO_META_SUFFIX = {
    ".png": ".png.meta",
    ".pac": ".pac.meta",
}


def parse_format_token(token: str) -> Dict[str, Any]:
    """解析格式标记，如 'astc_8x8:thorough' 或 'png:80'。"""
    token = token.strip()
    if not token:
        raise ValueError("Empty format token")
    if ":" in token:
        name_part, quality_part = token.split(":", 1)
        name = name_part.strip()
        quality_raw = quality_part.strip()
        if not name:
            raise ValueError(f"Invalid format token: {token}")
        quality = int(quality_raw) if quality_raw.isdigit() else float(quality_raw) if quality_raw.replace('.', '', 1).isdigit() else quality_raw
        return {"name": name, "quality": quality}
    return {"name": token}


def normalize_formats(raw_formats: Any) -> List[Dict[str, Any]]:
    """规范化格式定义列表。"""
    if raw_formats is None:
        return []

    normalized = []
    if isinstance(raw_formats, (list, tuple)):
        for entry in raw_formats:
            if isinstance(entry, dict):
                if "name" not in entry:
                    raise ValueError(f"Format definition missing name: {entry}")
                normalized.append(dict(entry))
            elif isinstance(entry, str):
                normalized.append(parse_format_token(entry))
            else:
                raise ValueError(f"Unsupported format definition type: {type(entry)}")
    elif isinstance(raw_formats, str):
        normalized.append(parse_format_token(raw_formats))
    else:
        raise ValueError("Invalid formats configuration")

    return [fmt.copy() for fmt in normalized]


def is_texture_meta(meta_data: Dict[str, Any]) -> bool:
    """判断 meta 是否为纹理资源。"""
    importer = meta_data.get("importer")
    asset_type = meta_data.get("type")
    return importer in {"texture", "texture-packer", "auto-atlas", "sprite-atlas"} or asset_type == "sprite"


def desired_minigame_settings(formats: Any) -> Dict[str, Any]:
    """生成目标小游戏格式设置。"""
    return {
        "formats": normalize_formats(formats),
    }


def ensure_platform_settings(meta_data: Dict[str, Any], formats: Any) -> bool:
    """确保 minigame 平台设置与目标格式匹配。"""
    platform_settings = meta_data.setdefault("platformSettings", {})
    desired = desired_minigame_settings(formats)
    current_minigame = platform_settings.get("minigame")

    if current_minigame == desired:
        return False

    platform_settings["minigame"] = desired
    return True


def reset_platform_settings(meta_data: Dict[str, Any]) -> bool:
    """重置 minigame 平台设置。"""
    platform_settings = meta_data.get("platformSettings")
    if not isinstance(platform_settings, dict):
        return False

    if "minigame" not in platform_settings:
        return False

    platform_settings.pop("minigame", None)
    if not platform_settings:
        meta_data["platformSettings"] = {}
    return True


def update_meta_file(meta_path: Path, reset: bool, formats: Any = None) -> bool:
    """更新单个 meta 文件。"""
    try:
        with meta_path.open("r", encoding="utf-8") as handle:
            meta_data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False

    if not is_texture_meta(meta_data):
        return False

    changed = reset_platform_settings(meta_data) if reset else ensure_platform_settings(meta_data, formats)
    if not changed:
        return False

    try:
        with meta_path.open("w", encoding="utf-8") as handle:
            json.dump(meta_data, handle, indent=2)
            handle.write("\n")
    except OSError:
        return False

    return True


def resolve_target_path(root: Path, target: str) -> Path:
    """解析目标路径。"""
    candidate = Path(target)
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def iter_supported_meta(search_root: Path) -> List[Path]:
    """遍历支持的后缀文件。"""
    seen = set()
    result = []
    for suffix in SUPPORTED_META_SUFFIXES:
        for meta_path in search_root.rglob(f"*{suffix}"):
            if meta_path not in seen:
                seen.add(meta_path)
                result.append(meta_path)
    return result


def process_meta_files(
    root: Path,
    targets: Optional[List[str]],
    formats: Any,
    reset: bool
) -> Dict[str, Any]:
    """批量处理 meta 文件。"""
    root_path = root if isinstance(root, Path) else Path(root)
    root_path = root_path.resolve()

    effective_formats = None
    if not reset:
        effective_formats = normalize_formats(formats)

    scanned_files = 0
    updated_files = 0
    updated_paths = []

    seen = set()
    target_paths = []

    if targets:
        for raw_target in targets:
            target_path = resolve_target_path(root_path, raw_target)

            if not target_path.exists():
                print(f"Warning: target path does not exist: {target_path}", file=sys.stderr)
                continue

            if target_path.is_dir():
                for meta_file in iter_supported_meta(target_path):
                    if meta_file not in seen:
                        seen.add(meta_file)
                        target_paths.append(meta_file)
                continue

            if target_path.is_file():
                lower_name = target_path.name.lower()
                if lower_name.endswith(SUPPORTED_META_SUFFIXES):
                    if target_path not in seen:
                        seen.add(target_path)
                        target_paths.append(target_path)
                    continue

                source_suffix = target_path.suffix.lower()
                meta_suffix = SOURCE_TO_META_SUFFIX.get(source_suffix)
                if meta_suffix:
                    meta_candidate = Path(str(target_path) + ".meta")
                    if meta_candidate.exists() and meta_candidate.name.lower().endswith(meta_suffix):
                        if meta_candidate not in seen:
                            seen.add(meta_candidate)
                            target_paths.append(meta_candidate)
                continue
    else:
        target_paths = iter_supported_meta(root_path)

    for meta_path in target_paths:
        scanned_files += 1
        if update_meta_file(meta_path, reset, effective_formats):
            updated_files += 1
            updated_paths.append(str(meta_path))

    return {
        "scanned": scanned_files,
        "updated": updated_files,
        "paths": updated_paths,
    }


def apply_texture_formats(project_root: str, paths: List[str] = None, formats: Any = None) -> Dict[str, Any]:
    """
    应用纹理压缩格式到指定路径。

    Args:
        project_root: 项目根目录
        paths: 目标路径列表
        formats: 格式定义

    Returns:
        统计信息字典
    """
    root = Path(project_root)
    return process_meta_files(root, paths, formats, reset=False)


def reset_texture_formats(project_root: str, paths: List[str] = None) -> Dict[str, Any]:
    """
    重置纹理压缩格式。

    Args:
        project_root: 项目根目录
        paths: 目标路径列表

    Returns:
        统计信息字典
    """
    root = Path(project_root)
    return process_meta_files(root, paths, formats=None, reset=True)


# ─────────────────── 配置驱动的压缩规则 ───────────────────

def apply_texture_compression_rules(project_root: str, config: dict, is_prod: bool = True) -> List[dict]:
    """
    根据配置应用纹理压缩规则。

    Args:
        project_root: 项目根目录
        config: 纹理压缩配置
        is_prod: 是否为正式环境

    Returns:
        已应用的规则列表
    """
    if not config:
        return []

    if not config.get('enabled', True):
        print('[texture-compress] 配置未启用，跳过。')
        return []

    # 选择正式或调试配置
    if is_prod:
        rules = config.get('rules', [])
    else:
        debug_cfg = config.get('debugRules') or config.get('textureCompressionForDebug', {})
        if isinstance(debug_cfg, dict):
            if debug_cfg.get('enabled'):
                rules = debug_cfg.get('rules', [])
            else:
                rules = config.get('rules', [])
        else:
            rules = config.get('rules', [])

    if not isinstance(rules, list):
        print('[texture-compress] rules 配置非法，跳过。')
        return []

    applied_rules = []
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            print(f'[texture-compress] 跳过非法规则 #{idx}: {rule}')
            continue

        rule_paths = rule.get('paths', [])
        paths = _normalize_rule_paths(rule_paths)
        if not paths:
            continue

        formats = rule.get('formats')
        try:
            summary = apply_texture_formats(project_root, paths, formats=formats)
        except ValueError as exc:
            raise RuntimeError(f"[texture-compress] 规则 #{idx} 生效失败: {exc}")

        if summary.get('updated'):
            print(f"[texture-compress] 规则 #{idx} 应用完成，修改 {summary['updated']} 个 meta。")
        applied_rules.append({'paths': paths, 'formats': formats})

    return applied_rules


def reset_texture_compression_rules(project_root: str, applied_rules: List[dict]):
    """恢复纹理压缩规则。"""
    if not applied_rules:
        return
    for record in applied_rules:
        paths = record.get('paths')
        try:
            reset_texture_formats(project_root, paths)
        except Exception as exc:
            print(f"[texture-compress] 恢复失败 {paths}: {exc}")


def _normalize_rule_paths(raw_paths: List) -> List[str]:
    """规范化规则路径列表。"""
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
