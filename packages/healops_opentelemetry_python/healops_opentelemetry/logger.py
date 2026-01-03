import requests
import json
import threading
import time
import queue
import logging
import sys
import traceback
from datetime import datetime
from typing import Optional, Dict, Any, List

from .utils import get_caller_info, clean_stack_trace, extract_file_path_from_stack

class HealOpsLogger:
    """HealOps Logger for sending logs directly to the backend with batching support"""
    
    def __init__(self,
                 api_key: str,
                 service_name: str,
                 endpoint: str = "https://engine.healops.ai",
                 source: str = "python",
                 enable_batching: bool = True,
                 batch_size: int = 50,
                 batch_interval_ms: int = 1000,
                 environment: Optional[str] = None,
                 release: Optional[str] = None):

        self.api_key = api_key
        self.service_name = service_name
        self.endpoint = endpoint
        self.source = source
        self.environment = environment
        self.release = release

        # Batching configuration
        self.enable_batching = enable_batching
        self.batch_size = max(1, min(batch_size, 1000))
        self.batch_interval_ms = max(100, min(batch_interval_ms, 60000))

        self.log_queue = queue.Queue()
        self.is_running = False
        self._batch_thread = None

        if self.enable_batching:
            self._start_batch_processor()

    def _start_batch_processor(self):
        self.is_running = True
        self._batch_thread = threading.Thread(target=self._process_batch, daemon=True)
        self._batch_thread.start()

    def _process_batch(self):
        """Background thread to process logs in batches"""
        batch = []
        last_flush = time.time() * 1000

        while self.is_running or not self.log_queue.empty():
            try:
                # Wait for log with timeout to ensure interval flush
                timeout = max(0, (self.batch_interval_ms - (time.time() * 1000 - last_flush)) / 1000.0)
                try:
                    payload = self.log_queue.get(timeout=timeout)
                    batch.append(payload)
                except queue.Empty:
                    pass

                current_time = time.time() * 1000
                time_diff = current_time - last_flush

                # Flush if batch size reached or interval passed
                if len(batch) >= self.batch_size or (len(batch) > 0 and time_diff >= self.batch_interval_ms):
                    self._flush_batch(batch)
                    batch = []
                    last_flush = current_time

            except Exception as e:
                # Prevent thread from dying
                if os.getenv("HEALOPS_DEBUG"):
                    print(f"HealOps batch processor error: {e}")
                time.sleep(1)

    def _flush_batch(self, batch: List[Dict]):
        """Send a batch of logs to backend"""
        if not batch:
            return

        url = f"{self.endpoint}/ingest/logs/batch"
        try:
            response = requests.post(
                url,
                json={"logs": batch},
                headers={
                    "X-HealOps-Key": self.api_key,
                    "Content-Type": "application/json"
                },
                timeout=5
            )
            response.raise_for_status()
            if os.getenv("HEALOPS_DEBUG"):
                 print(f"HealOps flushed {len(batch)} logs")
        except Exception as e:
            # Fallback to individual sending if batch fails
            if os.getenv("HEALOPS_DEBUG"):
                print(f"Batch send failed, falling back to individual: {e}")
            for log in batch:
                self._send_single_log(log)

    def destroy(self):
        """Cleanup resources"""
        self.is_running = False
        if self._batch_thread:
            self._batch_thread.join(timeout=2.0)

    def info(self, message: str, metadata: Optional[Dict[str, Any]] = None):
        """Send an INFO level log"""
        self._log("INFO", message, metadata)
    
    def warn(self, message: str, metadata: Optional[Dict[str, Any]] = None):
        """Send a WARNING level log"""
        self._log("WARNING", message, metadata)
    
    def error(self, message: str, metadata: Optional[Dict[str, Any]] = None):
        """Send an ERROR level log (will be persisted and may create incident)"""
        self._log("ERROR", message, metadata)
    
    def critical(self, message: str, metadata: Optional[Dict[str, Any]] = None):
        """Send a CRITICAL level log (will be persisted and may create incident)"""
        self._log("CRITICAL", message, metadata)
    
    def _log(self, severity: str, message: str, metadata: Optional[Dict[str, Any]] = None):
        """Internal method to prepare log payload"""

        # Get caller info
        caller_info = get_caller_info()

        # Enrich metadata
        enriched_metadata = metadata or {}
        enriched_metadata.update(caller_info)

        # Add stack trace for errors if not present
        if severity in ["ERROR", "CRITICAL"]:
            if "errorStack" not in enriched_metadata:
                # Capture current stack if not provided
                stack = "".join(traceback.format_stack())
                enriched_metadata["stack"] = clean_stack_trace(stack)

        # Add code attributes for OTel compatibility
        if "filePath" in enriched_metadata:
            enriched_metadata["code.file.path"] = enriched_metadata["filePath"]
        if "line" in enriched_metadata:
            enriched_metadata["code.line.number"] = enriched_metadata["line"]
        if "functionName" in enriched_metadata:
            enriched_metadata["code.function.name"] = enriched_metadata["functionName"]

        payload = {
            "service_name": self.service_name,
            "severity": severity,
            "message": str(message),
            "source": self.source,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": enriched_metadata
        }
        
        if self.environment:
            payload["environment"] = self.environment
        if self.release:
            payload["release"] = self.release

        if self.enable_batching:
            self.log_queue.put(payload)
        else:
            self._send_single_log(payload)

    def _send_single_log(self, payload: Dict[str, Any]):
        """Send a single log immediately"""
        try:
            response = requests.post(
                f"{self.endpoint}/ingest/logs",
                json=payload,
                headers={
                    "X-HealOps-Key": self.api_key,
                    "Content-Type": "application/json"
                },
                timeout=3
            )
            response.raise_for_status()
        except Exception as e:
            if os.getenv("HEALOPS_DEBUG"):
                print(f"HealOps Logger failed to send log: {e}")

import os

class HealOpsLogHandler(logging.Handler):
    """Python logging handler that sends logs to HealOps"""

    def __init__(self, logger: HealOpsLogger):
        super().__init__()
        self.healops_logger = logger

    def emit(self, record):
        try:
            msg = self.format(record)

            # Map python log levels to HealOps severity
            level_map = {
                logging.DEBUG: "INFO",
                logging.INFO: "INFO",
                logging.WARNING: "WARNING",
                logging.ERROR: "ERROR",
                logging.CRITICAL: "CRITICAL"
            }
            severity = level_map.get(record.levelno, "INFO")

            metadata = {
                "logger_name": record.name,
                "filePath": record.pathname,
                "line": record.lineno,
                "functionName": record.funcName,
                "process": record.process,
                "thread": record.threadName
            }

            if record.exc_info:
                metadata["errorStack"] = "".join(traceback.format_exception(*record.exc_info))
                metadata["exception"] = {
                    "type": record.exc_info[0].__name__,
                    "message": str(record.exc_info[1]),
                    "stacktrace": metadata["errorStack"]
                }

            # Call internal _log directly to avoid double caller info extraction
            # We already have caller info from the record
            self.healops_logger._log(severity, msg, metadata)

        except Exception:
            self.handleError(record)
