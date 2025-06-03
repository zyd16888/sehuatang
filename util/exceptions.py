"""
自定义异常类和异常处理工具
"""
import functools
import traceback
from typing import Callable, Any, Optional, Type
from util.log_util import log


class SHTBaseException(Exception):
    """项目基础异常类"""
    
    def __init__(self, message: str, error_code: Optional[str] = None):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)


class ConfigurationError(SHTBaseException):
    """配置错误"""
    pass


class NetworkError(SHTBaseException):
    """网络错误"""
    pass


class ParseError(SHTBaseException):
    """解析错误"""
    pass


class DatabaseError(SHTBaseException):
    """数据库错误"""
    pass


class NotificationError(SHTBaseException):
    """通知发送错误"""
    pass


class BrowserError(SHTBaseException):
    """浏览器操作错误"""
    pass


def handle_exceptions(
    default_return: Any = None,
    exceptions: tuple = (Exception,),
    log_error: bool = True,
    reraise: bool = False
):
    """
    异常处理装饰器
    
    Args:
        default_return: 异常时的默认返回值
        exceptions: 要捕获的异常类型
        log_error: 是否记录错误日志
        reraise: 是否重新抛出异常
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except exceptions as e:
                if log_error:
                    log.error(f"函数 {func.__name__} 执行出错: {e}")
                    log.error(f"详细错误: {traceback.format_exc()}")
                
                if reraise:
                    raise
                
                return default_return
        
        return wrapper
    return decorator


def handle_async_exceptions(
    default_return: Any = None,
    exceptions: tuple = (Exception,),
    log_error: bool = True,
    reraise: bool = False
):
    """
    异步函数异常处理装饰器
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except exceptions as e:
                if log_error:
                    log.error(f"异步函数 {func.__name__} 执行出错: {e}")
                    log.error(f"详细错误: {traceback.format_exc()}")
                
                if reraise:
                    raise
                
                return default_return
        
        return wrapper
    return decorator


class ExceptionHandler:
    """异常处理器类"""
    
    @staticmethod
    def handle_and_log(
        exception: Exception,
        context: str = "",
        reraise: bool = False
    ) -> None:
        """
        处理并记录异常
        
        Args:
            exception: 异常对象
            context: 异常上下文信息
            reraise: 是否重新抛出异常
        """
        error_msg = f"{context}: {str(exception)}" if context else str(exception)
        log.error(error_msg)
        log.error(f"异常详情: {traceback.format_exc()}")
        
        if reraise:
            raise exception
    
    @staticmethod
    def safe_execute(
        func: Callable,
        *args,
        default_return: Any = None,
        context: str = "",
        **kwargs
    ) -> Any:
        """
        安全执行函数
        
        Args:
            func: 要执行的函数
            *args: 函数参数
            default_return: 异常时的默认返回值
            context: 执行上下文
            **kwargs: 函数关键字参数
            
        Returns:
            函数执行结果或默认返回值
        """
        try:
            return func(*args, **kwargs)
        except Exception as e:
            ExceptionHandler.handle_and_log(e, context)
            return default_return
    
    @staticmethod
    async def safe_execute_async(
        func: Callable,
        *args,
        default_return: Any = None,
        context: str = "",
        **kwargs
    ) -> Any:
        """
        安全执行异步函数
        """
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            ExceptionHandler.handle_and_log(e, context)
            return default_return


# 常用的异常处理装饰器实例
network_error_handler = handle_exceptions(
    default_return=None,
    exceptions=(NetworkError, ConnectionError, TimeoutError),
    log_error=True
)

parse_error_handler = handle_exceptions(
    default_return=None,
    exceptions=(ParseError, ValueError, KeyError),
    log_error=True
)

database_error_handler = handle_exceptions(
    default_return=None,
    exceptions=(DatabaseError, ConnectionError),
    log_error=True
)
