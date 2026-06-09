"""
WSGI entry point for production deployment
"""

from main import app

# For Gunicorn
application = app

if __name__ == "__main__":
    import uvicorn
    from config import get_config
    
    config = get_config()
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=config.port,
        reload=False,
        workers=config.workers,
        log_level=config.log_level.lower()
    )
