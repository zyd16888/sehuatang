import time
import random
import threading
from queue import Queue, Empty
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor

from DrissionPage import ChromiumOptions, WebPage
from util.log_util import log
from util.config import domain, proxy_enable, proxy_url
from util.read_config import get_config

class BrowserAutomation:
    """
    重构后的浏览器自动化类 (Minimal & Clean Version)
    核心理念：统一 Page 和 Tab 的处理逻辑，减少重复代码。
    """

    def __init__(self, max_tabs: int = 3):
        self.proxy_enable = proxy_enable
        self.proxy_url = proxy_url
        self.max_tabs = max_tabs
        
        # 核心状态
        self.browser: Optional[WebPage] = None
        self.tab_pool: Queue = Queue()
        self._is_closing = False
        
        # 加载配置
        self.cfg = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """加载最基础的浏览器配置"""
        # 简单的环境判断 (保留原逻辑的简化版)
        is_docker = False
        try:
            import os
            if os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER'):
                is_docker = True
        except: pass

        base_args = [
            "--no-sandbox", "--disable-extensions"
        ]

        return {
            "timeout": 45 if is_docker else 30,
            "retry": 5 if is_docker else 3,
            "args": base_args,
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
        }

    def _init_browser(self):
        """懒加载初始化浏览器"""
        if self.browser: return

        co = ChromiumOptions()
        if self.proxy_enable and self.proxy_url:
            co.set_proxy(self.proxy_url)
        
        co.set_user_agent(self.cfg["ua"])
        
        # 确保每次启动环境全新
        co.incognito(True)  # 开启无痕模式
        co.auto_port()      # 使用随机端口，避免冲突/复用旧实例

        for arg in self.cfg["args"]:
            co.set_argument(arg)
            
        try:
            self.browser = WebPage(chromium_options=co)
            self._init_tabs()  # 启动时顺便初始化标签页池
            log.info("浏览器及标签页池初始化完成")
        except Exception as e:
            log.error(f"浏览器启动失败: {e}")
            raise

    def _init_tabs(self):
        """初始化多标签页资源池"""
        # 主页面作为第一个资源
        self.tab_pool.put(self.browser)
        # 创建额外的标签页
        for _ in range(self.max_tabs - 1):
            tab = self.browser.new_tab()
            self.tab_pool.put(tab)

    def close(self):
        """清理资源"""
        self._is_closing = True
        if self.browser:
            try:
                self.browser.quit()
            except: pass
            self.browser = None
        log.info("浏览器已关闭")

    # ==========================================
    # 核心逻辑：统一处理入口
    # ==========================================

    def _process_page(self, page_obj: WebPage, url: str) -> str:
        """
        [核心] 统一的页面处理流
        不管 page_obj 是主页面还是 Tab，都走这个流程
        """
        # 1. 访问
        page_obj.get(url)
        
        # 2. 智能等待
        self._smart_wait(page_obj)
        
        # 3. 反爬检测与处理
        self._handle_anti_bot(page_obj)
        
        # 4. 获取结果
        html = page_obj.html
        if not html or len(html) < 100:
            log.warning(f"页面内容异常 (len={len(html) if html else 0}): {url}")
            return ""
        return html

    def _smart_wait(self, page: WebPage):
        """优化的智能等待，适应不确定的网络环境"""
        cfg = self.cfg
        try:
            # 1. 等待页面开始加载 (解决点击后瞬间获取导致的旧页面内容问题)
            page.wait.load_start(timeout=5)
            
            # 2. 等待 DOM 加载完成 (这是第一步)
            page.wait.doc_loaded(timeout=cfg["timeout"])
            
            # 3. 动态检查页面稳定性 (轮询检查源码长度是否不再剧烈变化)
            last_len = 0
            stable_count = 0
            for _ in range(15):  # 最多额外等 7.5 秒
                curr_len = len(page.html)
                # 如果内容长度稳定(变化小于50字符)，或者已经有明显的内容(如 footer)
                if abs(curr_len - last_len) < 50 and curr_len > 1000:
                    stable_count += 1
                else:
                    stable_count = 0
                
                if stable_count >= 2: # 连续两次检查稳定
                    break
                
                last_len = curr_len
                time.sleep(0.5)

            # 4. 最后给予一个随机的微小缓冲，确保 JS 渲染彻底完成
            time.sleep(random.uniform(1.0, 2.5))
            
        except Exception as e:
            log.debug(f"智能等待过程中的非致命异常: {e}")
            # 出错时保底等待一下
            time.sleep(3)

    def _handle_anti_bot(self, page: WebPage):
        """统一的反爬处理"""
        try:
            title = page.title
            if not title: return

            # 1. Cloudflare 处理
            if title in ["Just a moment...", "请稍候…"] or "验证您是真人" in page.html:
                log.warning(f"检测到 Cloudflare 墙: {page.url}")
                log.warning(">>> 自动绕过已禁用。请在浏览器窗口中【手动点击】验证！ <<<")
                log.info("程序正在等待验证完成...")

                # 循环等待用户手动通过
                while True:
                    time.sleep(1)
                    try:
                        # 持续检查页面状态
                        if page.title not in ["Just a moment...", "请稍候…"] and "验证您是真人" not in page.html:
                            log.info(f"检测到页面已跳转至: {page.title}")
                            log.info("验证通过，继续执行...")
                            time.sleep(2) # 额外等待几秒让目标页面加载完全
                            break
                    except Exception:
                        pass
            
            # 2. 域名入口页处理 (串行执行，重新检查状态)
            # Cloudflare 验证通过后，可能会跳转到入口页，需要重新获取 title 和 html 进行判断
            try:
                time.sleep(2)  # 等待页面稳定
                curr_title = page.title
                curr_html = page.html
                
                # 判断条件：标题匹配 domain.upper() 或者 页面包含 "请点此进入"
                if (curr_title == domain.upper()) or ("请点此进入" in curr_html) or ("please click here" in curr_html.lower()):
                    log.info(f"检测到域名入口页 (Title: {curr_title})，尝试自动点击...")
                    btn = page.ele('.enter-btn')
                    if btn:
                        btn.click()
                        log.info("已点击进入按钮")
                        page.wait.load_start()
            except Exception as e:
                log.warning(f"入口页检测出错: {e}")

        except Exception as e:
            log.debug(f"反爬检测轻微异常(可忽略): {e}")

    # ==========================================
    # 公共方法 API
    # ==========================================

    def get_html(self, url: str) -> str:
        """获取单个页面的 HTML (使用标签页池中的任意一个)"""
        if self._is_closing: return ""
        self._init_browser()

        retries = self.cfg["retry"]
        for i in range(retries):
            tab = None
            try:
                # 从池中借出一个 Tab
                tab = self.tab_pool.get(timeout=30)
                return self._process_page(tab, url)
            except Empty:
                log.error("没有可用的标签页资源")
                return ""
            except Exception as e:
                log.warning(f"抓取失败 ({i+1}/{retries}): {e}")
                time.sleep(1)
            finally:
                # 无论成功失败，归还 Tab
                if tab: self.tab_pool.put(tab)
        return ""

    def get_batch_html(self, urls: List[str]) -> List[str]:
        """批量获取页面 HTML (增加频率限制)"""
        if not urls: return []
        self._init_browser()

        results = {}
        # 为了更精细控制，我们不再直接一次性扔进线程池
        with ThreadPoolExecutor(max_workers=self.max_tabs) as executor:
            future_to_url = {}
            for url in urls:
                # 提交任务，但在每个任务内部逻辑前会先随机等待
                future = executor.submit(self._get_html_with_delay, url)
                future_to_url[future] = url
                # 提交任务本身也稍微间隔一下，避免瞬间撑爆
                time.sleep(random.uniform(1.0, 3.0)) 
            
            # 获取结果
            for future in future_to_url:
                url = future_to_url[future]
                try:
                    results[url] = future.result()
                except Exception:
                    results[url] = ""
        
        return [results.get(u, "") for u in urls]

    def _get_html_with_delay(self, url: str) -> str:
        """带随机延迟的抓取，用于批量抓取时的频率控制"""
        # 每个线程开始前先随机抖动，避免多个标签页同时发起 GET
        wait_time = random.uniform(2.0, 5.0)
        log.debug(f"并发控制：等待 {wait_time:.1f}s 后访问 {url}")
        time.sleep(wait_time)
        return self.get_html(url)

    # 上下文管理器支持
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): self.close()

if __name__ == "__main__":
    # 简单测试
    with BrowserAutomation(max_tabs=1) as browser:
        # 单个测试
        print("Single Page:", len(browser.get_html("https://sehuatang.org")))
        
        # 批量测试
        urls = [
            "https://sehuatang.org/forum-103-1.html", 
            "https://sehuatang.org/forum-104-1.html"
        ]
        res = browser.get_batch_html(urls)
        print("Batch Pages:", [len(h) for h in res])
