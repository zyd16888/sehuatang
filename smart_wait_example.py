#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能等待功能示例
演示新的页面加载等待机制
"""

import time
from drissio import BrowserAutomation
from util.config import proxy_enable, proxy_url, domain


def example_smart_wait():
    """智能等待示例"""
    print("=== 智能等待功能示例 ===")
    
    # 配置智能等待参数
    smart_config = {
        "wait_strategy": "smart",  # 使用智能等待
        "doc_load_timeout": 15,    # 文档加载超时15秒
        "min_wait_time": 1,        # 最小等待1秒
        "max_wait_time": 2,        # 最大等待2秒
    }
    
    print("1. 使用智能等待模式:")
    start_time = time.time()
    
    with BrowserAutomation(
        proxy_enable=proxy_enable, 
        proxy_url=proxy_url, 
        max_tabs=2
    ) as browser:
        # 手动更新配置
        browser.browser_config.update(smart_config)
        
        # 测试单页面智能等待
        url = f"https://{domain}/forum-103-1.html"
        html = browser.get_page_html_multi_tab(url)
        
        print(f"   页面加载完成，HTML长度: {len(html) if html else 0}")
        print(f"   智能等待耗时: {time.time() - start_time:.2f}秒")


def example_random_wait():
    """随机等待示例（对比）"""
    print("\n2. 使用随机等待模式（对比）:")
    
    # 配置随机等待参数
    random_config = {
        "wait_strategy": "random",  # 使用随机等待
        "sleep_range": [3, 5],      # 随机等待3-5秒
    }
    
    start_time = time.time()
    
    with BrowserAutomation(
        proxy_enable=proxy_enable, 
        proxy_url=proxy_url, 
        max_tabs=2
    ) as browser:
        # 手动更新配置
        browser.browser_config.update(random_config)
        
        # 测试单页面随机等待
        url = f"https://{domain}/forum-103-1.html"
        html = browser.get_page_html_multi_tab(url)
        
        print(f"   页面加载完成，HTML长度: {len(html) if html else 0}")
        print(f"   随机等待耗时: {time.time() - start_time:.2f}秒")


def example_batch_smart_wait():
    """批量页面智能等待示例"""
    print("\n3. 批量页面智能等待:")
    
    urls = [
        f"https://{domain}/forum-103-1.html",
        f"https://{domain}/forum-103-2.html",
        f"https://{domain}/forum-104-1.html"
    ]
    
    smart_config = {
        "wait_strategy": "smart",
        "doc_load_timeout": 10,
        "min_wait_time": 0.5,
        "max_wait_time": 1.5,
    }
    
    start_time = time.time()
    
    with BrowserAutomation(
        proxy_enable=proxy_enable, 
        proxy_url=proxy_url, 
        max_tabs=3
    ) as browser:
        browser.browser_config.update(smart_config)
        
        # 批量获取页面
        html_list = browser.get_multiple_pages_html(urls)
        success_count = sum(1 for html in html_list if html)
        
        print(f"   批量获取完成: {success_count}/{len(urls)} 成功")
        print(f"   总耗时: {time.time() - start_time:.2f}秒")
        print(f"   平均每页: {(time.time() - start_time) / len(urls):.2f}秒")


def example_configuration_options():
    """配置选项说明"""
    print("\n=== 配置选项说明 ===")
    
    config_info = {
        "wait_strategy": {
            "说明": "等待策略选择",
            "可选值": ["smart", "random"],
            "默认值": "smart",
            "推荐": "smart - 更快更可靠"
        },
        "doc_load_timeout": {
            "说明": "文档加载超时时间（秒）",
            "范围": "5-30秒",
            "默认值": 15,
            "推荐": "10-15秒，根据网络情况调整"
        },
        "min_wait_time": {
            "说明": "最小等待时间（秒）",
            "范围": "0.5-3秒",
            "默认值": 1,
            "推荐": "1秒，确保页面稳定"
        },
        "max_wait_time": {
            "说明": "最大等待时间（秒）",
            "范围": "1-5秒",
            "默认值": 3,
            "推荐": "2-3秒，平衡速度和稳定性"
        }
    }
    
    for param, info in config_info.items():
        print(f"\n{param}:")
        for key, value in info.items():
            print(f"  {key}: {value}")


def main():
    """主函数"""
    print("智能页面加载等待功能演示")
    print("=" * 50)
    
    try:
        # 智能等待示例
        example_smart_wait()
        
        # 随机等待对比
        example_random_wait()
        
        # 批量处理示例
        example_batch_smart_wait()
        
        # 配置说明
        example_configuration_options()
        
        print("\n" + "=" * 50)
        print("演示完成！")
        print("\n主要优势:")
        print("✓ 更快的页面加载：基于实际页面状态而非固定时间")
        print("✓ 更高的可靠性：智能检测页面加载完成")
        print("✓ 更好的用户体验：减少不必要的等待时间")
        print("✓ 向后兼容：保留原有随机等待作为备用方案")
        
    except Exception as e:
        print(f"演示过程中出错: {e}")
        print("请检查网络连接和配置设置")


if __name__ == "__main__":
    main()
