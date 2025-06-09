#!/usr/bin/env python3
"""
Docker环境专用启动脚本
针对Docker环境进行了优化的启动流程
"""

import sys
import os
import time
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from util.log_util import log
from util.exceptions import ExceptionHandler


def check_docker_environment():
    """检查Docker环境"""
    log.info("检查Docker环境...")
    
    # 检查必要的环境变量
    required_env = ['PYTHONUNBUFFERED', 'PYTHONPATH']
    for env_var in required_env:
        value = os.getenv(env_var)
        log.info(f"环境变量 {env_var}: {value}")
    
    # 检查文件系统
    important_paths = [
        '/app',
        '/app/config',
        '/app/logs',
        '/.dockerenv'
    ]
    
    for path in important_paths:
        exists = os.path.exists(path)
        log.info(f"路径 {path}: {'存在' if exists else '不存在'}")
    
    # 检查网络连接
    try:
        import socket
        socket.setdefaulttimeout(5)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(('8.8.8.8', 53))
        log.info("网络连接: 正常")
        return True
    except Exception as e:
        log.error(f"网络连接: 异常 - {e}")
        return False


def run_docker_tests():
    """运行Docker环境测试"""
    log.info("运行Docker环境测试...")
    
    try:
        # 导入测试模块
        from test_docker_fix import main as test_main
        
        # 运行测试
        test_result = test_main()
        
        if test_result:
            log.info("✅ Docker环境测试通过")
            return True
        else:
            log.warning("⚠️ Docker环境测试部分失败")
            return False
            
    except Exception as e:
        log.error(f"Docker环境测试失败: {e}")
        return False


def run_main_application():
    """运行主应用程序"""
    log.info("启动主应用程序...")
    
    try:
        # 导入主程序
        from main import main as main_app
        import asyncio
        
        # 运行主程序
        asyncio.run(main_app())
        
        log.info("主应用程序执行完成")
        return True
        
    except Exception as e:
        log.error(f"主应用程序执行失败: {e}")
        ExceptionHandler.handle_and_log(e, "运行主应用程序时出错")
        return False


def run_scheduler():
    """运行调度器"""
    log.info("启动调度器模式...")
    
    try:
        from run import ApplicationRunner
        
        runner = ApplicationRunner()
        return runner.run_scheduler()
        
    except Exception as e:
        log.error(f"调度器运行失败: {e}")
        ExceptionHandler.handle_and_log(e, "运行调度器时出错")
        return False


def main():
    """主函数"""
    log.info("=" * 60)
    log.info("🐳 Docker环境专用启动脚本")
    log.info("=" * 60)
    
    # 检查运行模式
    mode = os.getenv('RUN_MODE', 'scheduler')
    log.info(f"运行模式: {mode}")
    
    # 检查Docker环境
    if not check_docker_environment():
        log.error("Docker环境检查失败")
        return False
    
    # 根据模式运行
    if mode == 'test':
        log.info("运行测试模式")
        return run_docker_tests()
    elif mode == 'once':
        log.info("运行单次模式")
        return run_main_application()
    elif mode == 'scheduler':
        log.info("运行调度器模式")
        return run_scheduler()
    else:
        log.error(f"未知的运行模式: {mode}")
        return False


if __name__ == "__main__":
    try:
        # 设置环境变量
        os.environ['PYTHONUNBUFFERED'] = '1'
        if 'PYTHONPATH' not in os.environ:
            os.environ['PYTHONPATH'] = '/app'
        
        success = main()
        
        if success:
            log.info("🎉 程序执行成功")
            sys.exit(0)
        else:
            log.error("❌ 程序执行失败")
            sys.exit(1)
            
    except KeyboardInterrupt:
        log.info("程序被用户中断")
        sys.exit(0)
    except Exception as e:
        log.error(f"程序执行过程中发生未预期的错误: {e}")
        ExceptionHandler.handle_and_log(e, "Docker启动脚本执行时出错")
        sys.exit(1)
