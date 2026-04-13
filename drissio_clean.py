import time
import os
import random
import threading
from queue import Queue, Empty
from typing import Optional, List, Dict, Any

from DrissionPage import ChromiumOptions, WebPage
from util.log_util import log
from util.config import domain, proxy_enable, proxy_url

class BrowserAutomation:
    """
    [重构版] 浏览器自动化类 - Worker 模式
    特性：
    1. 真·并发：每个 Tab 由一个独立线程(Worker)守护，同时执行任务。
    2. 全局协同：遇到 CF 验证时，所有 Worker 自动暂停，等待人工处理完成后集体恢复。
    """

    def __init__(self, max_tabs: int = 3):
        self.proxy_enable = proxy_enable
        self.proxy_url = proxy_url
        self.max_tabs = max_tabs

        # 核心组件
        self.browser: Optional[WebPage] = None

        # 线程协同工具
        self.task_queue = Queue()  # 任务队列
        self.results: Dict[str, str] = {}  # 结果存储 (URL -> HTML)
        self.run_event = threading.Event()  # 运行信号 (Set=跑, Clear=停)
        self.run_event.set()  # 默认为运行状态
        self.cf_lock = threading.Lock()  # CF 处理锁 (防止多个线程同时报错)

        self.workers: List[threading.Thread] = []
        self._is_initialized = False

        # 加载配置
        self.cfg = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """加载配置 - 适配 Docker 无头模式"""
        is_docker = False
        if os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER') == 'true':
            is_docker = True

        # 基础参数
        base_args = ["--no-sandbox", "--disable-extensions"]

        # Docker 专用配置
        if is_docker:
            # 在 Docker 中必须开启无头模式
            base_args.extend([
                "--headless=new",
                "--disable-gpu",
                "--disable-dev-shm-usage"
            ])
            log.info("Docker 环境：已强制开启 Headless 模式")

        return {
            "timeout": 45 if is_docker else 30,
            "retry": 5 if is_docker else 3,
            "args": base_args,
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
        }

    def _init_system(self):
        """初始化浏览器和所有 Worker 线程"""
        if self._is_initialized:
            return

        log.info(f"正在初始化浏览器及 {self.max_tabs} 个并发 Worker...")

        # 1. 初始化浏览器
        co = ChromiumOptions()
        if self.proxy_enable and self.proxy_url:
            co.set_proxy(self.proxy_url)
        co.set_user_agent(self.cfg["ua"])
        # co.incognito(True)
        co.auto_port()
        for arg in self.cfg["args"]:
            co.set_argument(arg)

        try:
            self.browser = WebPage(chromium_options=co)
        except Exception as e:
            log.error(f"浏览器启动失败: {e}")
            raise

        # 2. 创建并启动 Workers
        # 主页面给 Worker 0
        t0 = threading.Thread(
            target=self._worker_loop, args=(self.browser, 0), daemon=True
        )
        t0.start()
        self.workers.append(t0)

        # 其他 Worker 使用新标签页
        for i in range(1, self.max_tabs):
            new_tab = self.browser.new_tab()
            t = threading.Thread(
                target=self._worker_loop, args=(new_tab, i), daemon=True
            )
            t.start()
            self.workers.append(t)

        self._is_initialized = True
        log.info("所有 Worker 已就绪，等待任务...")

    def _worker_loop(self, page_obj: WebPage, worker_id: int):
        """Worker 线程主循环"""
        while True:
            # 1. 暂停机制：如果 run_event 被 clear，这里会阻塞，直到 CF 验证完成
            self.run_event.wait()

            # 2. 获取任务
            try:
                # timeout 设为 1 秒以便能定期检查 run_event 状态或退出
                url = self.task_queue.get(timeout=1)
            except Empty:
                continue
            except Exception:
                continue

            # 3. 执行任务
            try:
                # 随机抖动，避免瞬间并发特征过于明显 (3-5秒)
                time.sleep(random.uniform(3.0, 5.0))

                log.debug(f"[Worker-{worker_id}] 开始处理: {url}")
                html = self._process_page(page_obj, url)

                # 存储结果 (线程安全)
                # 注意：这里直接操作字典是线程安全的（在CPython中）
                self.results[url] = html

            except Exception as e:
                log.error(f"[Worker-{worker_id}] 处理异常: {e}")
                self.results[url] = ""

            finally:
                # 标记任务完成
                self.task_queue.task_done()

    def _process_page(self, page_obj: WebPage, url: str) -> str:
        """单页面处理逻辑 (包含 CF 协同处理)"""
        try:
            page_obj.get(url)
            self._smart_wait(page_obj)

            # 检查 Cloudflare 或 入口页
            if self._check_needs_handling(page_obj):
                self._handle_global_interruption(page_obj)

            html = page_obj.html
            return html if html and len(html) > 100 else ""
        except Exception as e:
            log.warning(f"页面处理失败: {e}")
            return ""

    def _check_needs_handling(self, page: WebPage) -> bool:
        """快速检查是否需要特殊处理"""
        try:
            t = page.title
            if not t:
                return False
            if t in ["Just a moment...", "请稍候…"]:
                return True
            if "验证您是真人" in page.html:
                return True
            if t == domain.upper():
                return True  # 入口页
            if "请点此进入" in page.html:
                return True
        except:
            return False
        return False

    def _handle_global_interruption(self, page_obj: WebPage):
        """
        处理全局中断 (CF 验证 / 入口页)
        关键逻辑：使用 Lock 确保只有一个 Worker 触发处理，其他 Worker 暂停
        """
        # 尝试获取锁。如果获取失败，说明已经有别的 Worker 在处理了，我们只需要等待
        if self.cf_lock.acquire(blocking=False):
            try:
                # === 我是那个被选中的 Worker，我来负责处理 ===
                log.warning(f"检测到访问阻碍 (CF/入口页)，暂停所有 Worker...")
                self.run_event.clear()  # 1. 暂停其他所有 Worker

                # 2. 执行具体的处理逻辑 (循环等待用户 / 自动点击)
                self._solve_interruption_logic(page_obj)

                log.info("阻碍已清除，恢复所有 Worker 工作")
                self.run_event.set()  # 3. 恢复运行
            finally:
                self.cf_lock.release()
        else:
            # === 我是其他 Worker，有人正在处理，我只需要等待 ===
            log.debug("其他 Worker 正在处理验证，本线程进入等待...")
            # 阻塞直到运行信号恢复
            self.run_event.wait()
            # 信号恢复后，刷新一下当前页面，确保状态最新
            try:
                page_obj.refresh()
                self._smart_wait(page_obj)
            except:
                pass

    def _solve_interruption_logic(self, page: WebPage):
        """具体的解决逻辑 (运行在锁内部)"""
        # Case A: 域名入口页 (自动点击)
        try:
            if page.title == domain.upper() or "请点此进入" in page.html:
                log.info("检测到入口页，尝试自动点击...")
                btn = page.ele(".enter-btn")
                if btn:
                    btn.click()
                    time.sleep(3)
                    return  # 处理完直接返回
        except:
            pass

        # Case B: Cloudflare (人工处理)
        # 如果不是入口页，或者入口页点击后变为了 CF
        log.warning(">>> 需要人工介入！请手动点击 Cloudflare 验证 <<<")
        while True:
            time.sleep(1)
            try:
                # 检查是否已跳转到正常页面
                # 正常页面特征：标题不是 "Just a moment..." 且不是 入口页
                curr_title = page.title
                if (
                    curr_title not in ["Just a moment...", "请稍候…", domain.upper()]
                    and "验证您是真人" not in page.html
                ):
                    log.info(f"验证通过，跳转至: {curr_title}")
                    time.sleep(2)
                    break

                # 如果是入口页，顺手点一下
                if curr_title == domain.upper():
                    btn = page.ele('.enter-btn')
                    if btn:
                        btn.click()
            except:
                pass

    def _smart_wait(self, page: WebPage):
        """智能等待"""
        try:
            page.wait.load_start(timeout=5)
            page.wait.doc_loaded(timeout=self.cfg["timeout"])
            time.sleep(random.uniform(0.5, 1.5))
        except:
            pass

    # ==========================================
    # 公共方法 API
    # ==========================================

    def get_html(self, url: str) -> str:
        """获取单页 HTML"""
        return self.get_batch_html([url])[0]

    def get_batch_html(self, urls: List[str]) -> List[str]:
        """
        批量获取 HTML
        生产者方法：将 URL 放入队列，等待 Worker 消费完成
        """
        if not urls: return []
        self._init_system()

        # 1. 清理旧结果 (可选，取决于是否需要累积)
        # self.results.clear() # 暂不清理，避免覆盖

        # 2. 生产任务
        log.info(f"发布 {len(urls)} 个任务到队列...")
        for url in urls:
            self.task_queue.put(url)

        # 3. 等待完成
        # queue.join() 会阻塞直到队列中所有任务都被 task_done()
        self.task_queue.join()

        # 4. 收集结果
        return [self.results.get(u, "") for u in urls]

    def close(self):
        """关闭资源"""
        if self.browser:
            try:
                self.browser.quit()
            except:
                pass
            self.browser = None
        log.info("浏览器资源已释放")

    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb): self.close()

if __name__ == "__main__":
    # 测试代码
    with BrowserAutomation(max_tabs=2) as browser:
        print("开始测试...")
        urls = [
            "https://sehuatang.org/forum-103-1.html",
            "https://sehuatang.org/forum-103-2.html",
            "https://sehuatang.org/forum-103-3.html",
            "https://sehuatang.org/forum-103-4.html",
        ]
        start = time.time()
        results = browser.get_batch_html(urls)
        end = time.time()

        print(f"耗时: {end-start:.2f}s")
        print(f"结果长度: {[len(r) for r in results]}")
