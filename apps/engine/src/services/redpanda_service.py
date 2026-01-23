"""
Redpanda Service - Handles Kafka-compatible streaming for log processing.
Provides producer and consumer functionality for sequential log processing.
"""
import json
import os
import asyncio
import threading
import time
from typing import Dict, Any, Optional, Callable
from datetime import datetime
from kafka import KafkaProducer, KafkaConsumer, TopicPartition
from kafka.errors import KafkaError, NoBrokersAvailable
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Redpanda Configuration
REDPANDA_BROKERS = os.getenv("REDPANDA_BROKERS", "localhost:19092")
REDPANDA_LOG_TOPIC = os.getenv("REDPANDA_LOG_TOPIC", "healops-logs")
REDPANDA_INCIDENT_TOPIC = os.getenv("REDPANDA_INCIDENT_TOPIC", "healops-incidents")

class RedpandaProducer:
    """Produces messages to Redpanda topics with automatic retries and error handling."""

    def __init__(self):
        self.producer = None
        self._connect_retries = 0
        self._max_retries = 5
        self._connect()

    def _connect(self):
        """Initialize Redpanda producer with error handling."""
        try:
            self.producer = KafkaProducer(
                bootstrap_servers=[REDPANDA_BROKERS],
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                key_serializer=lambda k: k.encode('utf-8') if k else None,
                retries=3,
                retry_backoff_ms=100,
                request_timeout_ms=10000,
                acks='all',  # Wait for all replicas to acknowledge
                batch_size=16384,
                linger_ms=10,  # Small batching for low latency
                compression_type='snappy'
            )
            logger.info(f"✓ Redpanda producer connected to {REDPANDA_BROKERS}")
            self._connect_retries = 0
        except NoBrokersAvailable as e:
            self._connect_retries += 1
            if self._connect_retries <= self._max_retries:
                logger.warning(f"⚠ Redpanda broker not available (attempt {self._connect_retries}/{self._max_retries}): {e}")
                self.producer = None
            else:
                logger.error(f"✗ Failed to connect to Redpanda after {self._max_retries} attempts")
                self.producer = None
        except Exception as e:
            logger.error(f"✗ Error initializing Redpanda producer: {e}")
            self.producer = None

    def publish_log(self, log_data: Dict[str, Any], key: Optional[str] = None) -> bool:
        """
        Publish a log message to the logs topic.

        Args:
            log_data: Log data dictionary
            key: Optional message key (service_name recommended for partitioning)

        Returns:
            bool: True if published successfully, False otherwise
        """
        if not self.producer:
            # Try to reconnect if producer is not available
            self._connect()
            if not self.producer:
                logger.error("Cannot publish log: Redpanda producer not available")
                return False

        try:
            # Use service_name as key for consistent partitioning
            message_key = key or log_data.get('service_name', 'unknown')

            # Add metadata for processing
            enriched_log = {
                **log_data,
                'redpanda_timestamp': datetime.utcnow().isoformat(),
                'topic': REDPANDA_LOG_TOPIC
            }

            # Send message
            future = self.producer.send(
                REDPANDA_LOG_TOPIC,
                value=enriched_log,
                key=message_key
            )

            # Optional: Wait for confirmation (can be removed for async)
            record_metadata = future.get(timeout=1)
            logger.debug(f"Log published to topic {record_metadata.topic}, partition {record_metadata.partition}, offset {record_metadata.offset}")
            return True

        except Exception as e:
            logger.error(f"✗ Failed to publish log to Redpanda: {e}")
            # Try to reconnect for next attempt
            self._connect()
            return False

    def publish_incident_task(self, task_data: Dict[str, Any], key: Optional[str] = None) -> bool:
        """
        Publish an incident processing task to the incidents topic.

        Args:
            task_data: Task data dictionary with log_id, incident_id, etc.
            key: Optional message key (service_name or incident_id recommended)

        Returns:
            bool: True if published successfully, False otherwise
        """
        if not self.producer:
            self._connect()
            if not self.producer:
                logger.error("Cannot publish incident task: Redpanda producer not available")
                return False

        try:
            message_key = key or str(task_data.get('log_id', 'unknown'))

            enriched_task = {
                **task_data,
                'redpanda_timestamp': datetime.utcnow().isoformat(),
                'topic': REDPANDA_INCIDENT_TOPIC
            }

            future = self.producer.send(
                REDPANDA_INCIDENT_TOPIC,
                value=enriched_task,
                key=message_key
            )

            record_metadata = future.get(timeout=1)
            logger.debug(f"Incident task published to topic {record_metadata.topic}, partition {record_metadata.partition}, offset {record_metadata.offset}")
            return True

        except Exception as e:
            logger.error(f"✗ Failed to publish incident task to Redpanda: {e}")
            self._connect()
            return False

    def close(self):
        """Close the producer connection."""
        if self.producer:
            self.producer.close()
            logger.info("Redpanda producer closed")


class RedpandaConsumer:
    """Consumes messages from Redpanda topics with automatic offset management."""

    def __init__(self, topic: str, group_id: str, message_handler: Callable[[Dict[str, Any]], None]):
        self.topic = topic
        self.group_id = group_id
        self.message_handler = message_handler
        self.consumer = None
        self.running = False
        self.consumer_thread = None
        self._connect_retries = 0
        self._max_retries = 5

    def _connect(self):
        """Initialize Redpanda consumer with error handling."""
        try:
            self.consumer = KafkaConsumer(
                self.topic,
                bootstrap_servers=[REDPANDA_BROKERS],
                group_id=self.group_id,
                value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                key_deserializer=lambda k: k.decode('utf-8') if k else None,
                auto_offset_reset='latest',  # Start from latest for new consumers
                enable_auto_commit=True,
                auto_commit_interval_ms=1000,
                max_poll_records=10,  # Process in small batches for responsiveness
                consumer_timeout_ms=1000  # Exit gracefully if no messages
            )
            logger.info(f"✓ Redpanda consumer connected to topic '{self.topic}' with group '{self.group_id}'")
            self._connect_retries = 0
        except NoBrokersAvailable as e:
            self._connect_retries += 1
            if self._connect_retries <= self._max_retries:
                logger.warning(f"⚠ Redpanda broker not available for consumer (attempt {self._connect_retries}/{self._max_retries}): {e}")
                self.consumer = None
            else:
                logger.error(f"✗ Failed to connect consumer to Redpanda after {self._max_retries} attempts")
                self.consumer = None
        except Exception as e:
            logger.error(f"✗ Error initializing Redpanda consumer: {e}")
            self.consumer = None

    def start_consuming(self):
        """Start consuming messages in a background thread."""
        if self.running:
            logger.warning("Consumer is already running")
            return

        self.running = True
        self.consumer_thread = threading.Thread(target=self._consume_loop, daemon=True)
        self.consumer_thread.start()
        logger.info(f"Started Redpanda consumer for topic '{self.topic}'")

    def _consume_loop(self):
        """Main consumer loop - runs in background thread."""
        while self.running:
            try:
                if not self.consumer:
                    self._connect()
                    if not self.consumer:
                        time.sleep(5)  # Wait before retry
                        continue

                # Poll for messages
                message_batch = self.consumer.poll(timeout_ms=1000)

                if not message_batch:
                    continue

                # Process messages sequentially
                for topic_partition, messages in message_batch.items():
                    for message in messages:
                        if not self.running:
                            break

                        try:
                            # Process message
                            self.message_handler(message.value)

                        except Exception as e:
                            logger.error(f"✗ Error processing message from offset {message.offset}: {e}")
                            # Continue processing other messages
                            continue

            except Exception as e:
                logger.error(f"✗ Error in consumer loop: {e}")
                time.sleep(5)  # Wait before retry
                self._connect()  # Try to reconnect

        logger.info("Consumer loop stopped")

    def stop_consuming(self):
        """Stop consuming messages and close consumer."""
        self.running = False
        if self.consumer_thread:
            self.consumer_thread.join(timeout=5)

        if self.consumer:
            self.consumer.close()
            logger.info(f"Redpanda consumer for topic '{self.topic}' stopped")


class RedpandaService:
    """Main service class that manages producers and consumers for the application."""

    def __init__(self):
        self.producer = RedpandaProducer()
        self.log_consumer = None
        self.incident_consumer = None
        self.websocket_broadcaster = None

    def setup_log_consumer(self, websocket_broadcaster: Callable[[Dict[str, Any]], None]):
        """Setup consumer for log broadcasting to WebSockets."""
        self.websocket_broadcaster = websocket_broadcaster
        self.log_consumer = RedpandaConsumer(
            topic=REDPANDA_LOG_TOPIC,
            group_id="healops-websocket-broadcaster",
            message_handler=self._handle_log_message
        )

    def setup_incident_consumer(self, incident_processor: Callable[[Dict[str, Any]], None]):
        """Setup consumer for incident task processing."""
        self.incident_consumer = RedpandaConsumer(
            topic=REDPANDA_INCIDENT_TOPIC,
            group_id="healops-incident-processor",
            message_handler=incident_processor
        )

    def _handle_log_message(self, log_data: Dict[str, Any]):
        """Handle log messages from Redpanda for WebSocket broadcasting."""
        try:
            if self.websocket_broadcaster:
                # Remove Redpanda metadata before broadcasting
                clean_log_data = {k: v for k, v in log_data.items()
                                if not k.startswith('redpanda_') and k != 'topic'}
                asyncio.run_coroutine_threadsafe(
                    self.websocket_broadcaster(clean_log_data),
                    asyncio.get_event_loop()
                )
        except Exception as e:
            logger.error(f"✗ Error broadcasting log message: {e}")

    def start_consumers(self):
        """Start all configured consumers."""
        if self.log_consumer:
            self.log_consumer.start_consuming()
        if self.incident_consumer:
            self.incident_consumer.start_consuming()

    def stop_consumers(self):
        """Stop all consumers."""
        if self.log_consumer:
            self.log_consumer.stop_consuming()
        if self.incident_consumer:
            self.incident_consumer.stop_consuming()

    def is_healthy(self) -> bool:
        """Check if Redpanda connection is healthy."""
        return self.producer.producer is not None

    def close(self):
        """Close all connections."""
        self.stop_consumers()
        self.producer.close()


# Global instance (will be initialized in main.py)
redpanda_service = RedpandaService()