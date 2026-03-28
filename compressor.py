# -*- coding: utf-8 -*-
"""
压缩模块 - 负责将文件/文件夹压缩为带密码的 7z 格式，并按指定大小分卷。

架构说明:
    1. 使用 py7zr 进行 7z 压缩（AES-256 加密）
    2. 压缩完成后，将大文件按用户设定的分卷大小切割为 .001, .002, ... 文件
    3. 提供进度回调，供 GUI 更新进度条

安全性:
    - 压缩密码仅在内存中使用，不写入任何日志或临时文件
"""

import os
import math
import py7zr
from typing import List, Callable, Optional


def compress_to_7z(
    source_paths: List[str],
    output_path: str,
    password: Optional[str] = None,
    progress_callback: Optional[Callable[[str], None]] = None
) -> str:
    """
    将文件/文件夹列表压缩为单个 7z 文件。

    Args:
        source_paths: 要压缩的文件/文件夹路径列表
        output_path: 输出的 .7z 文件完整路径
        password: 解压密码（可选，为 None 则不加密）
        progress_callback: 进度回调函数，接收状态字符串

    Returns:
        生成的 .7z 文件路径
    """
    if progress_callback:
        progress_callback("正在压缩文件...")

    # 构建 py7zr 压缩参数
    # 当设置密码时，使用 AES-256 加密头部和内容
    filters = [{"id": py7zr.FILTER_LZMA2, "preset": 5}]

    with py7zr.SevenZipFile(
        output_path,
        mode='w',
        password=password if password else None,
        filters=filters,
        header_encryption=True if password else False
    ) as archive:
        for src in source_paths:
            if os.path.isdir(src):
                # 添加整个目录，保留目录结构
                base_name = os.path.basename(src)
                if progress_callback:
                    progress_callback(f"正在添加文件夹: {base_name}")
                archive.writeall(src, arcname=base_name)
            elif os.path.isfile(src):
                # 添加单个文件
                file_name = os.path.basename(src)
                if progress_callback:
                    progress_callback(f"正在添加文件: {file_name}")
                archive.write(src, arcname=file_name)

    if progress_callback:
        progress_callback("压缩完成。")

    return output_path


def split_file(
    file_path: str,
    volume_size_mb: int,
    progress_callback: Optional[Callable[[str, float], None]] = None
) -> List[str]:
    """
    将文件按指定大小切割为分卷文件。

    生成的分卷文件命名格式: 原文件名.001, .002, ...
    例如: archive.7z -> archive.7z.001, archive.7z.002, ...

    Args:
        file_path: 要切割的文件路径
        volume_size_mb: 每个分卷的大小（MB）
        progress_callback: 进度回调函数，接收 (状态字符串, 进度百分比 0-100)

    Returns:
        分卷文件路径列表（按顺序排列）
    """
    volume_size_bytes = volume_size_mb * 1024 * 1024  # 将 MB 转换为字节
    file_size = os.path.getsize(file_path)

    # 如果文件大小不超过分卷大小，无需切割
    if file_size <= volume_size_bytes:
        if progress_callback:
            progress_callback("文件无需分卷。", 100)
        return [file_path]

    # 计算总分卷数
    total_parts = math.ceil(file_size / volume_size_bytes)
    volume_paths = []

    if progress_callback:
        progress_callback(f"正在分卷，共 {total_parts} 个分卷...", 0)

    with open(file_path, 'rb') as f:
        for i in range(1, total_parts + 1):
            # 生成分卷文件名: archive.7z.001, archive.7z.002, ...
            part_path = f"{file_path}.{i:03d}"
            chunk = f.read(volume_size_bytes)

            with open(part_path, 'wb') as part_file:
                part_file.write(chunk)

            volume_paths.append(part_path)
            progress = (i / total_parts) * 100

            if progress_callback:
                progress_callback(
                    f"分卷 {i}/{total_parts} 完成",
                    progress
                )

    if progress_callback:
        progress_callback("分卷完成。", 100)

    return volume_paths


def compress_and_split(
    source_paths: List[str],
    output_dir: str,
    archive_name: str,
    password: Optional[str] = None,
    volume_size_mb: int = 10,
    progress_callback: Optional[Callable[[str, float], None]] = None
) -> List[str]:
    """
    完整的压缩 + 分卷流程。

    Args:
        source_paths: 要压缩的文件/文件夹路径列表
        output_dir: 输出目录
        archive_name: 压缩包基础名称（不含扩展名）
        password: 解压密码（可选）
        volume_size_mb: 分卷大小（MB）
        progress_callback: 进度回调 (状态字符串, 进度百分比 0-100)

    Returns:
        分卷文件路径列表（按顺序）
    """
    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)

    # 第一步：压缩为完整的 .7z 文件
    archive_path = os.path.join(output_dir, f"{archive_name}.7z")

    if progress_callback:
        progress_callback("步骤 1/2: 正在压缩...", 0)

    def compress_cb(msg: str):
        """压缩阶段的进度回调包装器"""
        if progress_callback:
            progress_callback(msg, -1)  # -1 表示不确定进度

    compress_to_7z(source_paths, archive_path, password, compress_cb)

    # 第二步：分卷切割
    if progress_callback:
        progress_callback("步骤 2/2: 正在分卷...", 50)

    volume_paths = split_file(archive_path, volume_size_mb, progress_callback)

    # 如果进行了分卷（生成了多个文件），删除原始完整压缩包
    if len(volume_paths) > 1 or (len(volume_paths) == 1 and volume_paths[0] != archive_path):
        if volume_paths[0] != archive_path:
            try:
                os.remove(archive_path)
            except OSError:
                pass  # 删除失败不影响主流程

    return volume_paths
