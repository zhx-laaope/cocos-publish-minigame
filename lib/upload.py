# -*- coding: utf-8 -*-
"""
publish_tool.lib.upload
====================
资源上传模块 - 支持阿里云 OSS
"""

import os
import sys
import threading
from typing import Optional, List, Dict

# 尝试导入 oss2，如果不存在则跳过 OSS 功能
try:
    import oss2
    HAS_OSS2 = True
except ImportError:
    HAS_OSS2 = False


def _calc_oss_file(file_path: str, local_file_path: str) -> str:
    """
    生成上传到 OSS 的对象 key 的相对路径部分。
    """
    try:
        parts = file_path.split('remote')
        if len(parts) >= 3:
            oss_path = parts[2]
        else:
            oss_path = os.path.relpath(file_path, local_file_path)
    except Exception:
        oss_path = os.path.relpath(file_path, local_file_path)
    oss_file = oss_path.replace('\\', '/').lstrip('/')
    return oss_file


def get_all_files(folder_path: str) -> List[Dict[str, str]]:
    """遍历文件夹获取所有文件。"""
    files = []
    for root, dirs, file_names in os.walk(folder_path):
        for file_name in file_names:
            file_path = os.path.join(root, file_name)
            files.append({'file_path': file_path, 'file_name': file_name})
    return files


def upload_res(
    version: str = '1.0.0.0',
    is_prod: bool = False,
    local_file_path: str = "",
    config: dict = None,
    workers: int = 8
):
    """
    上传资源到阿里云 OSS。

    Args:
        version: 版本号
        is_prod: 是否为正式环境
        local_file_path: 本地资源目录
        config: OSS 配置字典，包含 accessKeyId, accessKeySecret, endpoint, bucketName 等
        workers: 并发线程数
    """
    if not HAS_OSS2:
        print('[upload] 错误: 未安装 oss2 库，请运行: pip install oss2')
        return

    if not config or not config.get('enabled', False):
        print('[upload] OSS 上传未启用或配置无效')
        return

    access_key_id = config.get('accessKeyId', '')
    access_key_secret = config.get('accessKeySecret', '')
    endpoint = config.get('endpoint', '')
    bucket_name = config.get('bucketName', '')

    if not all([access_key_id, access_key_secret, endpoint, bucket_name]):
        print('[upload] OSS 配置不完整，跳过上传')
        return

    print('[upload] 正式开始上传文件-------')
    auth = oss2.Auth(access_key_id, access_key_secret)

    if not os.path.exists(local_file_path):
        print(f'[upload] 本地没有 remote 文件: {local_file_path}')
        return

    remote_path_config = config.get('remotePath', {})
    remote_path = remote_path_config.get('dev', 'dev/remote_assets')
    if is_prod:
        remote_path = remote_path_config.get('prod', 'prod/remote_assets')

    all_files = get_all_files(local_file_path)
    total_files = len(all_files)

    try:
        workers = int(workers)
    except Exception:
        workers = 1

    if workers <= 1 or total_files <= 1:
        bucket = oss2.Bucket(auth, endpoint, bucket_name)
        for index, file_data in enumerate(all_files):
            file_path = file_data.get('file_path', '')
            try:
                oss_file = _calc_oss_file(file_path, local_file_path)
                bucket.put_object_from_file(f'{remote_path}/{version}/remote/{oss_file}', file_path)
                progress = (index + 1) / total_files * 100
                sys.stdout.write(f'\r上传进度: {progress:.2f}% ({index + 1}/{total_files})')
                sys.stdout.flush()
            except Exception:
                print(f'\n文件上传失败!! {file_path}')
                return
        print('\n===============文件上传成功===============')
        return

    # 多线程上传
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
    except Exception:
        print('[upload] 并发模块不可用，回退到单线程上传')
        bucket = oss2.Bucket(auth, endpoint, bucket_name)
        for index, file_data in enumerate(all_files):
            file_path = file_data.get('file_path', '')
            try:
                oss_file = _calc_oss_file(file_path, local_file_path)
                bucket.put_object_from_file(f'{remote_path}/{version}/remote/{oss_file}', file_path)
                progress = (index + 1) / total_files * 100
                sys.stdout.write(f'\r上传进度: {progress:.2f}% ({index + 1}/{total_files})')
                sys.stdout.flush()
            except Exception:
                print(f'\n文件上传失败!! {file_path}')
                return
        print('\n===============文件上传成功===============')
        return

    thread_local = threading.local()

    def _get_bucket():
        b = getattr(thread_local, 'bucket', None)
        if b is None:
            b = oss2.Bucket(auth, endpoint, bucket_name)
            thread_local.bucket = b
        return b

    def _upload_one(file_path: str) -> Optional[str]:
        try:
            oss_file = _calc_oss_file(file_path, local_file_path)
            _get_bucket().put_object_from_file(f'{remote_path}/{version}/remote/{oss_file}', file_path)
            return None
        except Exception as e:
            return f'{file_path} -> {e}'

    uploaded = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_upload_one, fd.get('file_path', '')) for fd in all_files]
        for fut in as_completed(futures):
            err = fut.result()
            if err:
                print(f'\n文件上传失败!! {err}')
                return
            uploaded += 1
            progress = uploaded / total_files * 100
            sys.stdout.write(f'\r上传进度: {progress:.2f}% ({uploaded}/{total_files}) [workers={workers}]')
            sys.stdout.flush()
    print('\n===============文件上传成功===============')
