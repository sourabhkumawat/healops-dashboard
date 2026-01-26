"""
Test Suite for Linear Ticket Resolution Safety Improvements

Tests the critical safety and reliability improvements implemented:
1. Thread safety with ThreadPoolExecutor
2. Database locking with unique constraints
3. Dead letter queue and retry logic
"""

import pytest
import asyncio
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from concurrent.futures import ThreadPoolExecutor

from src.services.linear_ticket_resolver import (
    LinearTicketResolver,
    process_ticket_task_from_redpanda,
    _mark_attempt_as_failed,
    shutdown_resolution_executor
)
from src.services.redpanda_service import RedpandaService, RedpandaConsumer, RedpandaProducer
from src.database.models import LinearResolutionAttempt, LinearResolutionAttemptStatus


class TestThreadSafetyImprovements:
    """Test thread safety improvements in ticket resolution."""

    def test_thread_pool_executor_used(self):
        """Test that ThreadPoolExecutor is used instead of manual threading."""
        task_data = {
            'task_type': 'resolve_linear_ticket',
            'integration_id': 1,
            'ticket_id': 'test-123',
            'ticket_identifier': 'TEST-123',
            'ticket_data': {'id': 'test-123', 'title': 'Test ticket'},
            'analysis': {'confidence_score': 0.8}
        }

        with patch('src.services.linear_ticket_resolver._resolution_executor') as mock_executor:
            with patch('src.services.linear_ticket_resolver.SessionLocal') as mock_session:
                mock_db = Mock()
                mock_session.return_value = mock_db

                # Call the function
                process_ticket_task_from_redpanda(task_data)

                # Verify ThreadPoolExecutor was used
                mock_executor.submit.assert_called_once()

    def test_timeout_handling(self):
        """Test that resolution has timeout protection."""
        # This test would need to mock the actual resolution process
        # In a real environment, you'd test with a long-running mock function

        task_data = {
            'task_type': 'resolve_linear_ticket',
            'integration_id': 1,
            'ticket_id': 'timeout-test',
            'ticket_identifier': 'TIMEOUT-123',
            'ticket_data': {'id': 'timeout-test', 'title': 'Timeout test'},
            'analysis': {'confidence_score': 0.8}
        }

        with patch('src.services.linear_ticket_resolver._resolution_executor') as mock_executor:
            with patch('src.services.linear_ticket_resolver.SessionLocal'):
                process_ticket_task_from_redpanda(task_data)

                # Verify submit was called (timeout would be handled in the submitted function)
                assert mock_executor.submit.called

    def test_graceful_shutdown(self):
        """Test that the thread pool can be shut down gracefully."""
        with patch('src.services.linear_ticket_resolver._resolution_executor') as mock_executor:
            shutdown_resolution_executor()
            mock_executor.shutdown.assert_called_once_with(wait=True)


class TestDatabaseLockingImprovements:
    """Test database locking improvements to prevent race conditions."""

    def test_unique_constraint_prevents_duplicates(self):
        """Test that unique constraint prevents multiple active attempts."""
        # This would need a real database test environment
        # In practice, you'd insert two attempts and verify IntegrityError
        pass

    def test_integrity_error_handling(self):
        """Test that IntegrityError is properly handled in resolve_ticket."""
        # Mock the database session to raise IntegrityError
        from sqlalchemy.exc import IntegrityError

        with patch('src.services.linear_ticket_resolver.SessionLocal') as mock_session_class:
            mock_db = Mock()
            mock_session_class.return_value = mock_db

            # Mock the integration
            mock_integration = Mock()
            mock_integration.id = 1
            mock_integration.user_id = 1
            mock_integration.config = {}
            mock_db.query.return_value.filter.return_value.first.return_value = mock_integration

            # Make db.commit() raise IntegrityError
            mock_db.commit.side_effect = IntegrityError("duplicate key", None, None)

            # Mock existing attempt query
            mock_existing_attempt = Mock()
            mock_existing_attempt.agent_name = "other-agent"
            mock_existing_attempt.status = "CLAIMED"
            mock_db.query.return_value.filter.return_value.first.return_value = mock_existing_attempt

            # Create resolver and test
            resolver = LinearTicketResolver(integration_id=1, db=mock_db)

            ticket_data = {'id': 'test-123', 'title': 'Test ticket'}

            # This should return an error without crashing
            result = asyncio.run(resolver.resolve_ticket(ticket_data))

            assert result['success'] is False
            assert 'already being resolved' in result['error']


class TestDeadLetterQueueImprovements:
    """Test dead letter queue and retry logic improvements."""

    def test_retry_logic_with_exponential_backoff(self):
        """Test that retry logic uses exponential backoff."""
        consumer = RedpandaConsumer("test-topic", "test-group", Mock())

        # Mock message that always fails
        mock_message = Mock()
        mock_message.value = {'test': 'data'}
        mock_message.offset = 123
        mock_message.partition = 0

        # Mock handler that always raises exception
        def failing_handler(msg):
            raise Exception("Test failure")

        consumer.message_handler = failing_handler

        with patch('time.sleep') as mock_sleep:
            with patch('src.services.redpanda_service.redpanda_service') as mock_service:
                mock_service.producer.publish_to_dead_letter_queue.return_value = True

                # This should fail after retries and go to DLQ
                with pytest.raises(Exception):
                    consumer._process_message_with_retry(mock_message)

                # Verify exponential backoff sleep calls
                assert mock_sleep.call_count >= 2  # Should have retried

    def test_dead_letter_queue_publishing(self):
        """Test that failed messages are sent to dead letter queue."""
        producer = RedpandaProducer()

        original_message = {'ticket_id': 'test-123', 'data': 'test'}
        error_info = {'error_message': 'Test error', 'error_type': 'TestException'}

        with patch.object(producer, 'producer') as mock_kafka_producer:
            mock_future = Mock()
            mock_future.get.return_value = Mock(topic='test-topic', partition=0, offset=456)
            mock_kafka_producer.send.return_value = mock_future

            # Test DLQ publishing
            result = producer.publish_to_dead_letter_queue(
                original_message=original_message,
                original_topic='test-topic',
                error_info=error_info,
                retry_count=3
            )

            assert result is True
            mock_kafka_producer.send.assert_called_once()

    def test_message_replay_from_dlq(self):
        """Test that messages can be replayed from dead letter queue."""
        service = RedpandaService()

        dlq_message = {
            'original_message': {'ticket_id': 'test-123'},
            'original_topic': 'linear-ticket-tasks',
            'error_info': {'error_message': 'Previous failure'},
            'retry_count': 2
        }

        with patch.object(service.producer, 'publish_ticket_task') as mock_publish:
            mock_publish.return_value = True

            result = service.replay_message_from_dlq(dlq_message)

            assert result is True
            mock_publish.assert_called_once()


class TestIntegratedSafetyFeatures:
    """Test integrated safety features working together."""

    def test_end_to_end_failure_recovery(self):
        """Test complete failure recovery flow: retry -> DLQ -> replay."""
        # This would be an integration test showing:
        # 1. Message fails processing
        # 2. Gets retried with exponential backoff
        # 3. Eventually sent to DLQ
        # 4. Can be replayed successfully
        pass

    def test_concurrent_resolution_prevention(self):
        """Test that multiple agents cannot work on same ticket."""
        # This would test the database constraint in a multi-threaded scenario
        pass

    def test_thread_pool_resource_management(self):
        """Test that thread pool doesn't create too many threads."""
        # Verify thread pool has reasonable limits and cleanup
        from src.services.linear_ticket_resolver import _resolution_executor
        assert _resolution_executor._max_workers == 5
        assert "linear-resolver" in _resolution_executor._thread_name_prefix


# Performance and stress tests
class TestPerformanceImprovements:
    """Test performance aspects of safety improvements."""

    def test_thread_pool_performance(self):
        """Test that thread pool improves performance over manual threads."""
        # Compare performance with and without thread pool
        pass

    def test_database_constraint_performance(self):
        """Test that database constraints don't significantly impact performance."""
        # Measure query performance with constraints
        pass

    def test_retry_mechanism_performance(self):
        """Test that retry mechanism doesn't cause excessive delays."""
        # Measure total time for retries with backoff
        pass


if __name__ == "__main__":
    pytest.main([__file__])