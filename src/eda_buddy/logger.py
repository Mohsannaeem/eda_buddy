import logging
import os
from datetime import datetime

class EDABuddyLogger:
    def __init__(self, log_dir="logs", name="eda_buddy"):
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        self.log_file = os.path.join(log_dir, f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
        
        # Create logger
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        
        # File handler
        file_handler = logging.FileHandler(self.log_file)
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_formatter)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter('%(levelname)s: %(message)s')
        console_handler.setFormatter(console_formatter)
        
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

    def info(self, msg):
        self.logger.info(msg)

    def debug(self, msg):
        self.logger.debug(msg)

    def error(self, msg):
        self.logger.error(f"\x1b[31m{msg}\x1b[0m")

    def warning(self, msg):
        self.logger.warning(f"\x1b[33m{msg}\x1b[0m")

    def success(self, msg):
        self.logger.info(f"\x1b[32mSUCCESS: {msg}\x1b[0m")

    def header(self, msg):
        border = "=" * 60
        self.logger.info(f"\n{border}\n{msg.center(60)}\n{border}")
