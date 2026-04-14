# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
publish_tool.bin.publish
=======================
Cocos Creator 微信小游戏打包工具 - 主入口

用法:
    python publish.py -b -v 1.0.0          # 构建版本 1.0.0
    python publish.py -b -d -v 1.0.0      # 调试模式构建
    python publish.py -b -t -v 1.0.0      # 构建并压缩 PNG
    python publish.py -b -u -v 1.0.0      # 构建并上传资源
    python publish.py -b -z -v 1.0.0      # 构建并上传到微信平台
    python publish.py -s -v 1.0.0         # 本地预览模式

详细配置请参考 config/config.example.json
"""

import os
import sys
import json
import shutil
import subprocess
import socket
import platform
from optparse import OptionParser
from pathlib import Path

# 添加 lib 目录到路径
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.join(TOOL_DIR, '..', 'lib')
CONFIG_DIR = os.path.join(TOOL_DIR, '..', 'config')
sys.path.insert(0, os.path.abspath(LIB_DIR))

from utils import (
    log_info, log_success, log_warn, log_error, log_step,
    resolve_path, resolve_tool_dir, detect_platform,
    load_config, get_pngquant_path,
    copy_project_tree, cleanup_build_workspace,
    load_publish_ignore_paths, resolve_ignore_stash_root,
    stash_publish_ignore_assets, restore_publish_ignore_assets,
    restore_meta_with_git, update_wechatgame_project_config, format_time
)
from upload import upload_res
from texture import apply_texture_compression_rules, reset_texture_compression_rules
from split_scripts import split_start_scene_bundle


# ─────────────────── 飞书通知 ───────────────────

def fetch_feishu_token(app_id: str, app_secret: str) -> str:
    """获取飞书 tenant_access_token。"""
    if not app_id or not app_secret:
        return ''

    try:
        import requests
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        resp = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            token = data.get("tenant_access_token") or ""
            if token:
                log_success("[feishu] 成功获取 tenant_access_token")
            else:
                log_warn(f"[feishu] 获取 token 为空: {data}")
            return token
        log_error(f"[feishu] 获取 token 失败: {data}")
    except Exception as exc:
        log_error(f"[feishu] 获取 token 异常: {exc}")
    return ''


def upload_feishu_image(img_path: str, feishu_token: str):
    """上传图片到飞书。"""
    try:
        import requests
        url = "https://open.feishu.cn/open-apis/im/v1/images"
        headers = {"Authorization": f"Bearer {feishu_token}"}
        files = {"image": open(img_path, "rb")}
        data = {"image_type": "message"}
        resp = requests.post(url, headers=headers, files=files, data=data)
        resp_json = resp.json()
        if resp_json.get("code") == 0:
            return resp_json["data"]["image_key"]
        else:
            print(f"[feishu] 图片上传失败: {resp_json}")
    except Exception as e:
        print(f"[feishu] 图片上传异常: {e}")
    return None


def send_feishu_card_with_qr(version: str, qr_path: str, feishu_token: str, config: dict, is_prod: bool = True):
    """发送飞书卡片消息。"""
    webhook = config.get('webhook', '')
    if not webhook:
        log_warn("[feishu] 未配置 Webhook，跳过消息推送")
        return

    try:
        import requests
        server_type = "正式服" if is_prod else "测试服"
        img_key = None
        if qr_path and feishu_token and os.path.exists(qr_path):
            img_key = upload_feishu_image(qr_path, feishu_token)
            if img_key:
                log_success("体验版二维码上传飞书成功")
            else:
                log_warn("体验版二维码上传飞书失败，将继续发送但图片为空")

        img_var = {
            "img_key": img_key or "",
            "i18n_img_key": {"zh_cn": img_key or ""}
        }
        if not img_key:
            log_warn("[feishu] 未获取到 img_key，img_QRcode 将使用空占位。")

        template_id = config.get('qrcodeTemplateId', '')
        template_version = config.get('qrcodeTemplateVersion', '1.0.0')

        if not template_id:
            log_warn("[feishu] 未配置模板 ID，跳过卡片消息")
            return

        card = {
            "msg_type": "interactive",
            "card": {
                "type": "template",
                "data": {
                    "template_id": template_id,
                    "template_version_name": template_version,
                    "template_variable": {
                        "img_QRcode": img_var,
                        "lbl_versionCode": version,
                        "lbl_serverType": server_type
                    }
                },
                "elements": []
            }
        }

        header = {"Content-Type": "application/json;charset=UTF-8"}
        resp = requests.post(webhook, json=card, headers=header)
        opener = resp.json()
        if opener.get("StatusMessage") == "success":
            log_success("飞书卡片消息发送成功！")
        else:
            log_error(f"飞书卡片消息发送失败，原因：{opener}")
    except Exception as e:
        log_error(f"发送飞书消息异常: {e}")


# ─────────────────── 微信 CLI 操作 ───────────────────

def _check_wechat_cli_output(output_text: str) -> tuple:
    """检查微信 CLI 输出中是否包含错误标志。"""
    if not output_text:
        return True, ''
    error_indicators = ['[error]', '✖']
    for indicator in error_indicators:
        if indicator in output_text:
            return False, output_text.strip()
    return True, ''


def upload_minigame_with_cli(version: str, proj_root_dir: str, config: dict, options, is_prod: bool):
    """使用微信开发者工具 CLI 命令行上传小游戏。"""
    project_name = config.get('build', {}).get('projectName', 'wechatgame')
    project_path = os.path.join(proj_root_dir, project_name)
    desc = f"版本号 {version} ,服务器 {('正式服' if is_prod else '测试服')} 通过CLI上传"

    wechat_config = config.get('wechat', {})
    cli_path = wechat_config.get('cliPath', '')
    if not cli_path:
        cli_path = '/Applications/wechatwebdevtools.app/Contents/MacOS/cli'

    cli_path = resolve_path(cli_path)
    upload_cmd = [
        cli_path, 'upload',
        '--project', project_path,
        '-v', version,
        '-d', desc,
    ]
    log_step(f'开始上传微信小游戏: {" ".join(upload_cmd)}')

    try:
        proc = subprocess.run(upload_cmd, capture_output=True, text=True, timeout=300)
        combined_output = (proc.stdout or '') + (proc.stderr or '')
        if combined_output:
            log_info(f'[wechat-cli] 输出:\n{combined_output.strip()}')
        output_ok, err_msg = _check_wechat_cli_output(combined_output)
        if proc.returncode != 0 or not output_ok:
            log_error(f'小游戏通过 CLI 上传失败! returncode={proc.returncode}')
            if err_msg:
                log_error(f'[wechat-cli] 错误详情: {err_msg}')
            return
    except subprocess.TimeoutExpired:
        log_error('[wechat-cli] 上传超时(300s)!')
        return
    except Exception as exc:
        log_error(f'[wechat-cli] 上传异常: {exc}')
        return

    log_success('小游戏通过 CLI 上传成功!')

    # 生成预览二维码并通知
    feishu_config = config.get('feishu', {})
    feishu_app_id = getattr(options, 'feishu_app_id', '') or feishu_config.get('appId', '')
    feishu_app_secret = getattr(options, 'feishu_app_secret', '') or feishu_config.get('appSecret', '')
    feishu_token = getattr(options, 'feishu_token', None)
    if not feishu_token:
        feishu_token = fetch_feishu_token(feishu_app_id, feishu_app_secret)

    preview_and_notify_feishu(version, project_path, cli_path, feishu_token, feishu_config, is_prod)


def preview_and_notify_feishu(version: str, project_path: str, cli_path: str, feishu_token: str, config: dict, is_prod: bool):
    """生成预览二维码并推送到飞书。"""
    qr_path = os.path.join(os.path.dirname(project_path), f"wechatgame_preview_qr_{version}.png")
    preview_cmd = f'"{cli_path}" preview --project "{project_path}" --qr-format image --qr-output "{qr_path}"'
    log_step(f'执行微信体验版预览: {preview_cmd}')

    status = os.system(preview_cmd)
    if status == 0 and os.path.exists(qr_path):
        send_feishu_card_with_qr(version, qr_path, feishu_token, config, is_prod=is_prod)
    else:
        log_error('体验版预览或二维码生成失败！')


# ─────────────────── 首屏插件 ───────────────────

def apply_first_screen_plugin(build_root: str, config: dict):
    """应用首屏插件：将 build-temp/wechatgame 下的文件替换打包后的同名文件。"""
    plugin_config = config.get('firstScreenPlugin', {})
    if not plugin_config.get('enabled', False):
        return

    source_path = plugin_config.get('sourcePath', 'build-temp/wechatgame')
    plugin_source_dir = resolve_path(source_path)

    if not os.path.isdir(plugin_source_dir):
        log_warn(f"[first-screen-plugin] 首屏插件源目录不存在: {plugin_source_dir}")
        return

    project_name = config.get('build', {}).get('projectName', 'wechatgame')
    wx_proj_dir = os.path.join(build_root, project_name)
    if not os.path.isdir(wx_proj_dir):
        log_warn(f"[first-screen-plugin] 打包后的微信小游戏目录不存在: {wx_proj_dir}")
        return

    log_step('[first-screen-plugin] 开始应用首屏插件...')

    for item in os.listdir(plugin_source_dir):
        src_item_path = os.path.join(plugin_source_dir, item)
        dest_item_path = os.path.join(wx_proj_dir, item)

        if os.path.isfile(src_item_path):
            try:
                shutil.copy2(src_item_path, dest_item_path)
                log_info(f"[first-screen-plugin] 已替换文件: {item}")
            except Exception as exc:
                log_error(f"[first-screen-plugin] 替换文件失败 {item}: {exc}")
        elif os.path.isdir(src_item_path):
            try:
                if os.path.exists(dest_item_path):
                    shutil.rmtree(dest_item_path)
                shutil.copytree(src_item_path, dest_item_path)
                log_info(f"[first-screen-plugin] 已复制文件夹: {item}")
            except Exception as exc:
                log_error(f"[first-screen-plugin] 复制文件夹失败 {item}: {exc}")

    log_success('[first-screen-plugin] 首屏插件应用完成!')


# ─────────────────── PNG 压缩 ───────────────────

def compress_png(proj_root_dir: str, config: dict, target_dirs: list = None):
    """使用 pngquant 压缩 PNG 文件。"""
    pngquant_path = get_pngquant_path(config)
    if not pngquant_path or not os.path.exists(pngquant_path):
        log_error(f"[pngquant] 未找到 pngquant 可执行文件: {pngquant_path}")
        return

    pngquant_cfg = config.get('pngquant', {})
    quality = pngquant_cfg.get('quality', '80-100')

    dirs_to_walk = []
    if target_dirs:
        if isinstance(target_dirs, str):
            raw = [p.strip() for p in target_dirs.split(',') if p.strip()]
        else:
            raw = list(target_dirs)

        for p in raw:
            abs_p = p if os.path.isabs(p) else os.path.join(proj_root_dir, p)
            if os.path.isdir(abs_p):
                dirs_to_walk.append(abs_p)
            else:
                print(f'[pngquant] 跳过不存在的目录: {abs_p}')
    else:
        dirs_to_walk = [proj_root_dir]

    for base_dir in dirs_to_walk:
        for root, _, files in os.walk(base_dir):
            for file in files:
                split_path = os.path.splitext(file)
                if split_path[1].lower() == '.png':
                    png_path = os.path.join(root, file)
                    cmd = f'{pngquant_path} -f --quality {quality} --ext .png "{png_path}"'
                    try:
                        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                        output, _ = p.communicate()
                        status = p.returncode
                    except Exception as e:
                        log_error(f'[pngquant] 执行失败 {png_path}: {e}')
                        continue

                    if status != 0:
                        out = output.decode(errors='ignore') if isinstance(output, (bytes, bytearray)) else str(output)
                        log_error(f'[pngquant] 压缩失败: {png_path} | {out}')
                        continue

                    log_info(f'[pngquant] 压缩完成: {png_path}')

    log_success('pngquant压缩完毕')


# ─────────────────── 构建核心 ───────────────────

def del_dir(dir_path: str):
    """删除目录。"""
    if os.path.isdir(dir_path):
        shutil.rmtree(dir_path)


def create_proj(creator_path: str, creator_proj_path: str, proj_root_dir: str, version: str, config: dict) -> bool:
    """执行 Cocos Creator 构建。"""
    build_config = config.get('build', {})
    project_name = build_config.get('projectName', 'wechatgame')
    remote_dir_name = build_config.get('remoteDirName', 'remote')
    md5_cache = build_config.get('md5Cache', True)
    start_scene_asset_bundle = build_config.get('startSceneAssetBundle', True)

    if os.path.isdir(proj_root_dir):
        if proj_root_dir.endswith("/"):
            proj_root_dir = proj_root_dir[:-1]
        del_dir(os.path.join(proj_root_dir, remote_dir_name))

    build_params = (
        f'buildPath={proj_root_dir};'
        f'title={project_name}_wx_{version};'
        f'platform=wechatgame;'
        f'md5Cache={str(md5_cache).lower()};'
        f'startSceneAssetBundle={str(start_scene_asset_bundle).lower()}'
    )

    cmd = f'"{creator_path}" --path "{creator_proj_path}" --build "{build_params}"'
    print(f'执行构建: {cmd}')

    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output, err = p.communicate()
    status = p.returncode

    text_output = ""
    if output:
        try:
            text_output = output.decode('utf-8')
        except UnicodeDecodeError:
            text_output = output.decode('utf-8', errors='ignore')

    if text_output:
        text_output = text_output.replace('\r\n', '\n').replace('\\r\\n', '\n').replace('\\n', '\n')

    if status == 0:
        log_success('项目构建成功!')
        return True
    else:
        log_error(f'项目构建失败! 退出码: {status}')
        try:
            os.makedirs(TOOL_DIR, exist_ok=True)
            build_log_dir = os.path.join(TOOL_DIR, '..', '..', 'build_logs')
            os.makedirs(build_log_dir, exist_ok=True)
            timestamp = subprocess.check_output(['date', '+%Y%m%d_%H%M%S']).decode().strip()
            log_path = os.path.join(build_log_dir, f'build_fail_{timestamp}.txt')
            with open(log_path, 'w', encoding='utf-8') as log_file:
                log_file.write(text_output)
            log_error(f'日志已保存: {log_path}')
        except Exception:
            pass
        return False


def compress_project_json(proj_root_dir: str):
    """压缩工程 import 目录下的 json。"""
    for parent, _, files in os.walk(proj_root_dir):
        for file in files:
            name_ext = os.path.splitext(file)
            if name_ext[1] == '.json':
                target_file = os.path.join(parent, file)
                try:
                    with open(target_file, 'r') as rf:
                        json_info = json.load(rf)
                    with open(target_file, 'w') as wf:
                        json.dump(json_info, wf)
                except Exception:
                    pass
    log_success('json压缩完毕')


def remove_unnessary_assets(proj_root_dir: str, wx_proj_dir: str, config: dict):
    """移除不必要的资源。"""
    build_config = config.get('build', {})
    project_name = build_config.get('projectName', 'wechatgame')
    remote_dir_name = build_config.get('remoteDirName', 'remote')

    local_res = os.path.join(proj_root_dir, project_name, remote_dir_name)
    remote_res = os.path.join(proj_root_dir, remote_dir_name, remote_dir_name)

    if os.path.exists(remote_res):
        shutil.rmtree(remote_res)
    if os.path.exists(local_res):
        shutil.copytree(local_res, remote_res)
        shutil.rmtree(local_res)
        print(f'raw-assets 重新加载完毕, 资源已备份至: {remote_res}')


def config_cdn(config: dict, cdn_url: str, version: str, project_path: str = None):
    """配置 CDN 地址。"""
    if project_path is None:
        project_path = os.path.join(TOOL_DIR, '..', '..', 'settings', 'wechatgame.json')

    if not os.path.exists(project_path):
        print(f'[cdn] 配置文件不存在: {project_path}')
        return

    cdn_url_full = os.path.join(cdn_url, version)

    try:
        with open(project_path, 'r') as f:
            lines = []
            for line in f:
                lines.append(line)
        with open(project_path, 'w') as output:
            output.write(''.join(lines))
        print(f'[cdn] CDN 配置完毕: {cdn_url_full}')
    except Exception as e:
        print(f'[cdn] CDN 配置失败: {e}')


# ─────────────────── 主构建流程 ───────────────────

def build(proj_root_dir: str, tiny: bool, version: str, is_prod: bool, upload: bool, options, config: dict):
    """完整的构建流程。"""
    workspace = prepare_build_workspace(version, config)
    workspace_root = workspace['root']
    workspace_build_root = os.path.join(workspace_root, 'build', version)
    os.makedirs(os.path.dirname(workspace_build_root), exist_ok=True)

    ignore_config = config.get('ignoreResources', {})
    ignore_enabled = ignore_config.get('enabled', True)
    ignore_paths = load_publish_ignore_paths(ignore_config.get('paths', [])) if ignore_enabled else []
    ignored_records = []
    compression_records = []
    stash_root = resolve_ignore_stash_root(workspace_root, ignore_config.get('stashRoot'))

    try:
        if ignore_paths:
            ignored_records = stash_publish_ignore_assets(workspace_root, ignore_paths, stash_root)

        texture_config = config.get('textureCompression', {})
        if texture_config.get('enabled'):
            compression_records = apply_texture_compression_rules(workspace_root, texture_config, is_prod)

        creator_path = resolve_path(config.get('creator', {}).get('path', ''))
        build_ok = create_proj(creator_path, workspace_root, workspace_build_root, version, config)

        if not build_ok:
            log_error('检测到构建失败，已终止后续流程。')
            return False

        # 复制构建产物
        copy_build_output(workspace_build_root, proj_root_dir)

        # 后续处理
        build_config = config.get('build', {})
        project_name = build_config.get('projectName', 'wechatgame')
        wx_proj_dir = os.path.join(proj_root_dir, project_name)

        # 修改微信配置
        upload_options = config.get('wechat', {}).get('uploadOptions', {})
        update_wechatgame_project_config(
            proj_root_dir, project_name,
            libVersion=upload_options.get('libVersion', 'latest'),
            **{f'setting.{k}': v for k, v in upload_options.items()
               if k not in ['libVersion'] and k not in ['setting.*']}
        )
        update_wechatgame_project_config(
            proj_root_dir, project_name,
            **{f'setting.{k}': v for k, v in {
                'urlCheck': upload_options.get('urlCheck', True) if is_prod else False,
                'minified': upload_options.get('minified', True),
                'es6': upload_options.get('es6', False),
                'enhance': upload_options.get('enhance', False),
                'swc': upload_options.get('swc', True),
            }.items()}
        )

        compress_project_json(proj_root_dir)

        if tiny:
            compress_png(proj_root_dir, config, getattr(options, 'tiny_dirs', None))

        remove_unnessary_assets(proj_root_dir, wx_proj_dir, config)

        # 首屏插件
        apply_first_screen_plugin(proj_root_dir, config)

        # 拆分 start-scene
        split_config = config.get('splitStartScene', {})
        if split_config.get('enabled', True):
            log_step('[split-scripts] 开始拆分 start-scene 脚本...')
            split_result = split_start_scene_bundle(wx_proj_dir)
            if split_result:
                log_success(f'[split-scripts] 拆分完成! 首包缩减 {split_result["reduction_percent"]}% '
                            f'({split_result["reduction_bytes"]:,} bytes)')
            else:
                log_warn('[split-scripts] 脚本拆分未执行（可能已拆分或格式不兼容）')

        # 版本信息
        version_data = {'game_version': version, 'is_prod': is_prod}
        version_path = os.path.join(proj_root_dir, project_name, 'version.json')
        os.makedirs(os.path.dirname(version_path), exist_ok=True)
        with open(version_path, 'w') as f:
            json.dump(version_data, f)

        # 上传资源
        if upload:
            remote_dir = config.get('build', {}).get('remoteDirName', 'remote')
            remote_root = os.path.join(proj_root_dir, remote_dir)
            workers = config.get('upload', {}).get('workers', 8)
            oss_config = config.get('oss', {})
            upload_res(version, is_prod, remote_root, config=oss_config, workers=workers)

        # 上传到微信
        if options.upload_game:
            upload_minigame_with_cli(version, proj_root_dir, config, options, is_prod)

        return True

    finally:
        reset_texture_compression_rules(workspace_root, compression_records)
        restore_publish_ignore_assets(workspace_root, ignored_records, stash_root)

        if not workspace.get('is_copy'):
            try:
                normalized = load_publish_ignore_paths(ignore_config.get('paths', [])) if ignore_enabled else []
                restore_meta_with_git(os.path.dirname(TOOL_DIR), normalized)
            except Exception as exc:
                print(f"[git-meta-restore] 恢复 .meta 时发生异常: {exc}")

        if workspace.get('is_copy'):
            cleanup_build_workspace(workspace_root)


def prepare_build_workspace(version: str, config: dict) -> dict:
    """准备构建工作空间。"""
    build_copy_config = config.get('buildCopy', {})
    if not build_copy_config.get('enabled', True):
        return {'root': os.path.dirname(TOOL_DIR), 'is_copy': False}

    base_dir = resolve_path(build_copy_config.get('rootPath', '~/Downloads'))
    os.makedirs(base_dir, exist_ok=True)

    proj_name = os.path.basename(os.path.dirname(TOOL_DIR))
    prefix = build_copy_config.get('folderName', f'{proj_name}_build_copy')
    timestamp = subprocess.check_output(['date', '+%Y%m%d_%H%M%S']).decode().strip()
    workspace_root = os.path.join(base_dir, f"{prefix}_{version}_{timestamp}")

    excludes = build_copy_config.get('excludes', [])
    copy_project_tree(os.path.dirname(TOOL_DIR), workspace_root, excludes)
    return {'root': workspace_root, 'is_copy': True}


def copy_build_output(src_root: str, dest_root: str):
    """复制构建产物。"""
    abs_src = os.path.abspath(src_root)
    abs_dest = os.path.abspath(dest_root)
    if abs_src == abs_dest:
        return
    if not os.path.exists(abs_src):
        raise RuntimeError(f"[build-copy] 未找到构建产物: {abs_src}")
    if os.path.exists(abs_dest):
        shutil.rmtree(abs_dest)
    os.makedirs(os.path.dirname(abs_dest), exist_ok=True)
    shutil.copytree(abs_src, abs_dest)
    print(f"[build-copy] 构建结果已复制回: {abs_dest}")


# ─────────────────── CLI 入口 ───────────────────

def main():
    parser = OptionParser()

    # 加载配置
    config_path = os.path.join(CONFIG_DIR, 'config.json')
    if not os.path.exists(config_path):
        config_path = os.path.join(CONFIG_DIR, 'config.example.json')
    config = load_config(config_path)

    # 设置默认值
    parser.set_defaults(version='0')
    parser.set_defaults(release=True)
    parser.set_defaults(cdn=config.get('cdn', {}).get('default', ''))
    parser.set_defaults(tiny=False)
    parser.set_defaults(build=False)
    parser.set_defaults(serve=False)
    parser.set_defaults(upload=False)
    parser.set_defaults(upload_game=False)
    parser.set_defaults(feishu_token='')
    parser.set_defaults(feishu_app_id=config.get('feishu', {}).get('appId', ''))
    parser.set_defaults(feishu_app_secret=config.get('feishu', {}).get('appSecret', ''))
    parser.set_defaults(upload_workers=config.get('upload', {}).get('workers', 8))

    # 命令行参数
    parser.add_option('-v', '--version', type="string", dest='version', help='版本号')
    parser.add_option('-d', '--debug', action="store_false", dest='release', help='调试模式（不校验域名）')
    parser.add_option('-c', '--cdn', type="string", dest='cdn', help='指定 CDN 地址')
    parser.add_option('-t', '--tiny', action="store_true", dest='tiny', help='使用 pngquant 压缩 PNG')
    parser.add_option('-T', '--tiny-dirs', type="string", dest='tiny_dirs', help='指定要用 -t 压缩的目录，逗号分隔')
    parser.add_option('-b', '--build', action="store_true", dest='build', help='构建项目')
    parser.add_option('-s', '--serve', action="store_true", dest='serve', help='本地预览模式')
    parser.add_option('-u', '--upload', action="store_true", dest='upload', help='上传资源到 OSS')
    parser.add_option('--upload_workers', type="int", dest='upload_workers', help='OSS 上传并发线程数')
    parser.add_option('-z', '--upload_game', action="store_true", dest='upload_game', help='上传小游戏到微信平台')
    parser.add_option('--config', type="string", dest='config_path', help='指定配置文件路径')

    (options, args) = parser.parse_args()

    # 重新加载指定配置
    if getattr(options, 'config_path', None):
        config = load_config(options.config_path)
    else:
        # 使用环境变量或默认路径
        env_config = os.environ.get('PUBLISH_CONFIG')
        if env_config:
            config = load_config(env_config)

    start_time = __import__('time').time()
    log_info(f"参数: {options} {args}")
    log_step('========== 打包开始: %s ==========' % __import__('time').strftime("%Y-%m-%d %H:%M:%S", __import__('time').localtime(start_time)))

    build_config = config.get('build', {})
    build_path = resolve_path(build_config.get('outputPath', '../build'))
    proj_root_dir = os.path.join(build_path, options.version)

    if options.version != '0':
        if options.serve:
            host, _, ips = socket.gethostbyname_ex(socket.gethostname())
            for ip in ips:
                if ip.startswith('172.') or ip.startswith('192.'):
                    log_info(f'配置本地 CDN: http://{ip}:16333')
                    config_cdn(config, f'http://{ip}:16333', '')
                    break
        else:
            log_info(f'配置外部 CDN: {options.cdn}')
            config_cdn(config, options.cdn, options.version)

    build_ok = True
    if options.build:
        build_ok = build(proj_root_dir, options.tiny, options.version, options.release, options.upload, options, config)

    end_time = __import__('time').time()
    spend_time = format_time(end_time - start_time)

    if not build_ok:
        log_error('========== 打包失败: %s 耗时: %s ==========' % (
            __import__('time').strftime("%Y-%m-%d %H:%M:%S", __import__('time').localtime(end_time)),
            spend_time
        ))
        sys.exit(1)

    log_success('========== 打包完成: %s 耗时: %s ==========' % (
        __import__('time').strftime("%Y-%m-%d %H:%M:%S", __import__('time').localtime(end_time)),
        spend_time
    ))

    if options.serve:
        remote_dir = build_config.get('remoteDirName', 'remote')
        remote_path = os.path.join(proj_root_dir, remote_dir)
        print('remotePath', remote_path)
        os.chdir(remote_path)

        # 复制私有配置
        proj_path = os.path.dirname(TOOL_DIR)
        config_json = os.path.join(TOOL_DIR, '..', 'project.private.config.json')
        config_jsonc = os.path.join(TOOL_DIR, '..', 'project.private.config.jsonc')
        src_cfg = config_json if os.path.exists(config_json) else config_jsonc
        dst_cfg = os.path.join(proj_root_dir, build_config.get('projectName', 'wechatgame'), 'project.private.config.json')
        if os.path.exists(src_cfg):
            shutil.copy(src_cfg, dst_cfg)
        else:
            log_warn(f'未找到 {config_json} 或 {config_jsonc}，已跳过 project.private.config.json 复制。')

        print('启动本地资源服务器...')
        p = subprocess.Popen('python --version', shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, _ = p.communicate()
        if str(output).replace("b'", "").startswith('Python 3'):
            print('可在 remote 目录下通过 python -m http.server 16333 手动启动')
            os.system('python3 -m http.server 16333')
        else:
            print('可在 remote 目录下通过 python -m SimpleHTTPServer 16333 手动启动')
            os.system('python -m SimpleHTTPServer 16333')


if __name__ == '__main__':
    main()
