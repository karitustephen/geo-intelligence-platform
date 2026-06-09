"""
Logging configuration for the geospatial intelligence API
"""

import logging
import json
import sys
from datetime import datetime
from typing import Dict, Any, Optional


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging"""
    
    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        
        if hasattr(record, 'request_id'):
            log_entry["request_id"] = record.request_id
        
        if hasattr(record, 'user_id'):
            log_entry["user_id"] = record.user_id
        
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        if record.stack_info:
            log_entry["stack"] = self.formatStack(record.stack_info)
        
        return json.dumps(log_entry)


class RequestContextFilter(logging.Filter):
    """Add request context to log records"""
    
    def filter(self, record: logging.LogRecord) -> bool:
        import asyncio
        from middleware.auth_middleware import get_request_context
        
        context = get_request_context()
        record.request_id = context.get('request_id', 'unknown')
        record.user_id = context.get('user_id', 'anonymous')
        
        return True


def setup_logging(
    log_level: str = "INFO",
    log_format: str = "json",
    service_name: str = "geo-intelligence"
):
    """Configure logging for the application"""
    
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    
    # Set formatter
    if log_format.lower() == "json":
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(request_id)s - %(message)s'
        )
    
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # Add request context filter
    root_logger.addFilter(RequestContextFilter())
    
    # Set level for noisy loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    
    # Log startup message
    root_logger.info(f"Logging configured | level={log_level} | format={log_format} | service={service_name}")


class LoggerContext:
    """Context manager for request-scoped logging context"""
    
    def __init__(self, request_id: str, user_id: Optional[str] = None):
        self.request_id = request_id
        self.user_id = user_id
        self._old_context = None
    
    def __enter__(self):
        import asyncio
        from middleware.auth_middleware import set_request_context
        
        self._old_context = get_request_context()
        set_request_context({
            'request_id': self.request_id,
            'user_id': self.user_id or 'anonymous'
        })
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        from middleware.auth_middleware import set_request_context
        set_request_context(self._old_context)


def get_logger(name: str) -> logging.Logger:
    """Get logger with context support"""
    return logging.getLogger(name)