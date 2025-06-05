#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多标签页功能使用示例
演示如何在实际项目中使用多标签页功能提高抓取效率
"""

import asyncio
import time
from typing import List
from drissio import BrowserAutomation
from scrapers.web_scraper import WebScraper
from util.log_util import log
from util.config import proxy_enable, proxy_url, domain


def example_basic_usage():
    """基础使用示例"""
    print("=== 基础使用示例 ===")
    
    # 方式1: 直接使用BrowserAutomation
    print("\n1. 直接使用BrowserAutomation类:")
    
    with BrowserAutomation(proxy_enable=proxy_enable, proxy_url=proxy_url, max_tabs=3) as browser:
        # 单个页面抓取
        url = f"https://{domain}/forum-103-1.html"
        html = browser.get_page_html_multi_tab(url)
        print(f"   单页面抓取 - HTML长度: {len(html) if html else 0}")
        
        # 批量页面抓取（推荐）
        urls = [
            f"https://{domain}/forum-103-1.html",
            f"https://{domain}/forum-103-2.html",
            f"https://{domain}/forum-104-1.html"
        ]
        html_list = browser.get_multiple_pages_html(urls)
        print(f"   批量抓取 - 成功: {sum(1 for h in html_list if h)}/{len(urls)}")


def example_performance_comparison():
    """性能对比示例"""
    print("\n=== 性能对比示例 ===")
    
    test_urls = [
        f"https://{domain}/forum-103-1.html",
        f"https://{domain}/forum-103-2.html",
        f"https://{domain}/forum-104-1.html",
        f"https://{domain}/forum-104-2.html"
    ]
    
    # 单标签页模式
    print("\n测试单标签页模式...")
    start_time = time.time()
    with BrowserAutomation(proxy_enable=proxy_enable, proxy_url=proxy_url, max_tabs=1) as browser:
        single_results = []
        for url in test_urls:
            html = browser.get_page_html(url)
            single_results.append(len(html) if html else 0)
    single_time = time.time() - start_time
    
    # 多标签页模式
    print("测试多标签页模式...")
    start_time = time.time()
    with BrowserAutomation(proxy_enable=proxy_enable, proxy_url=proxy_url, max_tabs=3) as browser:
        multi_results = browser.get_multiple_pages_html(test_urls)
        multi_results = [len(html) if html else 0 for html in multi_results]
    multi_time = time.time() - start_time
    
    # 结果对比
    print(f"\n性能对比结果:")
    print(f"   单标签页: {single_time:.2f}秒")
    print(f"   多标签页: {multi_time:.2f}秒")
    if multi_time > 0:
        speedup = single_time / multi_time
        print(f"   性能提升: {speedup:.2f}倍")


async def example_webscraper_integration():
    """WebScraper集成示例"""
    print("\n=== WebScraper集成示例 ===")
    
    # WebScraper会自动根据配置使用多标签页
    with WebScraper() as scraper:
        print(f"多标签页模式: {'启用' if scraper.enable_multi_tab else '禁用'}")
        print(f"最大标签页数: {scraper.max_tabs}")
        
        # 初始化主页
        if scraper.initialize_homepage():
            print("主页初始化成功")
            
            # 测试板块抓取（只抓取少量数据用于演示）
            fid = 103  # 高清中文字幕板块
            
            try:
                # 获取板块信息
                start_time = time.time()
                plate_info_list, tid_list = await scraper._get_plate_info_batch(fid)
                plate_time = time.time() - start_time
                
                print(f"板块信息获取完成:")
                print(f"   耗时: {plate_time:.2f}秒")
                print(f"   帖子数量: {len(tid_list)}")
                
                # 获取前3个帖子的详细信息（演示用）
                if plate_info_list:
                    test_info_list = plate_info_list[:3]
                    start_time = time.time()
                    detailed_data = await scraper._get_thread_details_batch(test_info_list)
                    detail_time = time.time() - start_time
                    
                    print(f"帖子详情获取完成:")
                    print(f"   耗时: {detail_time:.2f}秒")
                    print(f"   成功数量: {len([d for d in detailed_data if d])}")
                    
                    # 显示部分结果
                    for i, data in enumerate(detailed_data[:2]):
                        if data:
                            title = data.get('title', 'N/A')[:30]
                            print(f"   帖子{i+1}: {title}...")
                
            except Exception as e:
                print(f"抓取过程中出错: {e}")
        else:
            print("主页初始化失败")


def example_custom_configuration():
    """自定义配置示例"""
    print("\n=== 自定义配置示例 ===")
    
    # 测试不同的标签页配置
    configs = [
        {"max_tabs": 1, "name": "单标签页"},
        {"max_tabs": 2, "name": "双标签页"},
        {"max_tabs": 3, "name": "三标签页"},
        {"max_tabs": 5, "name": "五标签页"}
    ]
    
    test_urls = [
        f"https://{domain}/forum-103-1.html",
        f"https://{domain}/forum-104-1.html"
    ]
    
    for config in configs:
        print(f"\n测试{config['name']}配置:")
        try:
            start_time = time.time()
            with BrowserAutomation(
                proxy_enable=proxy_enable, 
                proxy_url=proxy_url, 
                max_tabs=config['max_tabs']
            ) as browser:
                if config['max_tabs'] == 1:
                    # 单标签页使用传统方法
                    results = []
                    for url in test_urls:
                        html = browser.get_page_html(url)
                        results.append(len(html) if html else 0)
                else:
                    # 多标签页使用批量方法
                    html_list = browser.get_multiple_pages_html(test_urls)
                    results = [len(html) if html else 0 for html in html_list]
                
                elapsed_time = time.time() - start_time
                success_count = sum(1 for r in results if r > 0)
                
                print(f"   耗时: {elapsed_time:.2f}秒")
                print(f"   成功: {success_count}/{len(test_urls)}")
                print(f"   结果: {results}")
                
        except Exception as e:
            print(f"   配置测试失败: {e}")


def example_error_handling():
    """错误处理示例"""
    print("\n=== 错误处理示例 ===")
    
    # 测试无效URL的处理
    invalid_urls = [
        "https://invalid-domain-12345.com",
        f"https://{domain}/invalid-page.html",
        "https://httpstat.us/500"  # 返回500错误的测试URL
    ]
    
    print("测试错误处理机制:")
    with BrowserAutomation(proxy_enable=proxy_enable, proxy_url=proxy_url, max_tabs=2) as browser:
        try:
            results = browser.get_multiple_pages_html(invalid_urls)
            
            for i, (url, result) in enumerate(zip(invalid_urls, results)):
                status = "成功" if result else "失败"
                length = len(result) if result else 0
                print(f"   URL{i+1}: {status} (长度: {length})")
                
        except Exception as e:
            print(f"   批量处理异常: {e}")


def main():
    """主函数"""
    print("多标签页功能使用示例")
    print("=" * 50)
    
    try:
        # 基础使用示例
        example_basic_usage()
        
        # 性能对比示例
        example_performance_comparison()
        
        # WebScraper集成示例
        asyncio.run(example_webscraper_integration())
        
        # 自定义配置示例
        example_custom_configuration()
        
        # 错误处理示例
        example_error_handling()
        
        print("\n" + "=" * 50)
        print("所有示例执行完成！")
        
    except Exception as e:
        log.error(f"示例执行过程中出错: {e}")
        print(f"示例执行失败: {e}")


if __name__ == "__main__":
    main()
