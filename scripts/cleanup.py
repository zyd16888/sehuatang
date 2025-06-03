#!/usr/bin/env python3
"""
项目清理脚本
清理临时文件、日志文件等
"""
import os
import shutil
from pathlib import Path
from util.log_util import log


def cleanup_temp_files():
    """清理临时文件"""
    project_root = Path(__file__).parent.parent
    
    # 要清理的文件模式
    temp_patterns = [
        "*.tmp",
        "*.temp",
        "xxxx*.html",
        "2.html"
    ]
    
    # 要清理的目录
    temp_dirs = [
        "__pycache__",
        ".pytest_cache",
        "temp",
        "tmp"
    ]
    
    cleaned_files = 0
    cleaned_dirs = 0
    
    # 清理文件
    for pattern in temp_patterns:
        for file_path in project_root.rglob(pattern):
            try:
                file_path.unlink()
                cleaned_files += 1
                log.info(f"删除文件: {file_path}")
            except Exception as e:
                log.error(f"删除文件失败 {file_path}: {e}")
    
    # 清理目录
    for dir_name in temp_dirs:
        for dir_path in project_root.rglob(dir_name):
            if dir_path.is_dir():
                try:
                    shutil.rmtree(dir_path)
                    cleaned_dirs += 1
                    log.info(f"删除目录: {dir_path}")
                except Exception as e:
                    log.error(f"删除目录失败 {dir_path}: {e}")
    
    log.info(f"清理完成: 删除了 {cleaned_files} 个文件和 {cleaned_dirs} 个目录")


def cleanup_old_logs(days=7):
    """清理旧日志文件"""
    import time
    
    logs_dir = Path(__file__).parent.parent / "logs"
    if not logs_dir.exists():
        return
    
    cutoff_time = time.time() - (days * 24 * 60 * 60)
    cleaned_logs = 0
    
    for log_file in logs_dir.glob("*.log*"):
        try:
            if log_file.stat().st_mtime < cutoff_time:
                log_file.unlink()
                cleaned_logs += 1
                log.info(f"删除旧日志: {log_file}")
        except Exception as e:
            log.error(f"删除日志文件失败 {log_file}: {e}")
    
    log.info(f"清理了 {cleaned_logs} 个旧日志文件")


if __name__ == "__main__":
    log.info("开始清理项目文件...")
    cleanup_temp_files()
    cleanup_old_logs()
    log.info("项目清理完成")
