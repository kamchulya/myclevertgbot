import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

def run_web_server():
    port = int(os.getenv("PORT", 8080))
    static_dir = Path(__file__).parent / "static"
    
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(static_dir), **kwargs)
        
        def log_message(self, format, *args):
            pass  # тихий режим
    
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

def start_web_in_background():
    thread = threading.Thread(target=run_web_server, daemon=True)
    thread.start()
