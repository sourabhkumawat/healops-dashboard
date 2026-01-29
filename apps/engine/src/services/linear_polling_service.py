"""
Linear Ticket Polling Service

Background service that polls Linear boards for new tickets and processes them
for automated resolution using the existing agent infrastructure.
"""
import os
import asyncio
import signal
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from dataclasses import dataclass

from src.database.database import SessionLocal
from src.database.models import Integration, LinearResolutionAttempt
from src.services.linear_ticket_resolver import LinearTicketWorkflowManager
try:
    from src.services.email import send_test_email
    EMAIL_AVAILABLE = True
except ImportError:
    EMAIL_AVAILABLE = False
    print("⚠️  Email service not available")


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("linear_polling_service")


@dataclass
class PollingConfig:
    """Configuration for the polling service."""
    # Polling frequency
    polling_interval_minutes: int = 15  # Check every 15 minutes

    # Processing limits
    max_tickets_per_integration: int = 5
    max_concurrent_integrations: int = 3

    # Error handling
    max_consecutive_errors: int = 5
    error_backoff_minutes: int = 30

    # Monitoring
    enable_email_alerts: bool = True
    admin_email: Optional[str] = None

    # Service control
    shutdown_timeout_seconds: int = 300  # 5 minutes
    health_check_interval_minutes: int = 60  # 1 hour


class LinearPollingService:
    """Background service for automated Linear ticket processing."""

    def __init__(self, config: Optional[PollingConfig] = None):
        self.config = config or PollingConfig()
        self.running = False
        self.shutdown_requested = False
        self.consecutive_errors = 0
        self.last_successful_cycle = None
        self.cycle_count = 0
        self.stats = {
            "total_cycles": 0,
            "total_tickets_processed": 0,
            "total_successful_resolutions": 0,
            "total_failed_resolutions": 0,
            "uptime_start": None,
            "last_cycle_duration": None
        }

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.shutdown_requested = True

    async def start(self):
        """Start the polling service."""
        logger.info("🚀 Starting Linear Ticket Polling Service")

        self.running = True
        self.stats["uptime_start"] = datetime.utcnow()

        try:
            # Initial health check
            await self._health_check()

            # Start main polling loop
            await self._polling_loop()

        except Exception as e:
            logger.error(f"❌ Fatal error in polling service: {e}")
            raise
        finally:
            self.running = False
            logger.info("🛑 Linear Ticket Polling Service stopped")

    async def _polling_loop(self):
        """Main polling loop."""
        health_check_task = None

        try:
            # Start health check task
            health_check_task = asyncio.create_task(self._periodic_health_check())

            while not self.shutdown_requested:
                cycle_start = datetime.utcnow()

                try:
                    logger.info(f"🔄 Starting polling cycle #{self.cycle_count + 1}")

                    # Run resolution cycle
                    await self._run_resolution_cycle()

                    # Update stats
                    self.last_successful_cycle = datetime.utcnow()
                    self.consecutive_errors = 0
                    self.cycle_count += 1
                    self.stats["total_cycles"] += 1

                    cycle_duration = (datetime.utcnow() - cycle_start).total_seconds()
                    self.stats["last_cycle_duration"] = cycle_duration

                    logger.info(f"✅ Completed polling cycle #{self.cycle_count} in {cycle_duration:.1f}s")

                except Exception as e:
                    self.consecutive_errors += 1
                    logger.error(f"❌ Error in polling cycle #{self.cycle_count + 1}: {e}")

                    # Check if we've hit the error threshold
                    if self.consecutive_errors >= self.config.max_consecutive_errors:
                        logger.error(f"💥 Max consecutive errors reached ({self.consecutive_errors}), entering backoff mode")

                        if self.config.enable_email_alerts:
                            await self._send_error_alert(e)

                        # Wait longer before next attempt
                        await self._wait_with_shutdown_check(self.config.error_backoff_minutes * 60)
                        continue

                # Wait for next polling interval
                await self._wait_with_shutdown_check(self.config.polling_interval_minutes * 60)

        finally:
            if health_check_task and not health_check_task.done():
                health_check_task.cancel()
                try:
                    await health_check_task
                except asyncio.CancelledError:
                    pass

    async def _run_resolution_cycle(self):
        """Run a single resolution cycle."""
        db = SessionLocal()

        try:
            workflow_manager = LinearTicketWorkflowManager(db)

            # Run the resolution cycle
            results = await workflow_manager.run_resolution_cycle(
                max_tickets_per_integration=self.config.max_tickets_per_integration
            )

            # Update statistics
            self.stats["total_tickets_processed"] += results.get("total_resolutions_attempted", 0)
            self.stats["total_successful_resolutions"] += results.get("total_successful_resolutions", 0)
            self.stats["total_failed_resolutions"] += results.get("total_failed_resolutions", 0)

            # Log summary
            logger.info(f"📊 Cycle summary: {results['total_successful_resolutions']}/{results['total_resolutions_attempted']} successful, "
                       f"{results['integrations_processed']} integrations processed")

            # Check for any critical errors
            if results.get("errors"):
                logger.warning(f"⚠️  Cycle completed with {len(results['errors'])} errors")
                for error in results["errors"][:3]:  # Log first 3 errors
                    logger.warning(f"  - {error}")

        finally:
            db.close()

    async def _wait_with_shutdown_check(self, seconds: int):
        """Wait for specified seconds while checking for shutdown requests."""
        end_time = datetime.utcnow() + timedelta(seconds=seconds)

        while datetime.utcnow() < end_time and not self.shutdown_requested:
            await asyncio.sleep(min(5, (end_time - datetime.utcnow()).total_seconds()))

    async def _health_check(self):
        """Perform health check of the service and dependencies."""
        logger.info("🏥 Running health check...")

        db = SessionLocal()
        try:
            # Check database connectivity
            db.query(Integration).filter(Integration.provider == "LINEAR").count()
            logger.info("✅ Database connectivity: OK")

            # Check for active Linear integrations
            active_integrations = db.query(Integration).filter(
                Integration.provider == "LINEAR",
                Integration.status == "ACTIVE"
            ).count()

            if active_integrations == 0:
                logger.warning("⚠️  No active Linear integrations found")
            else:
                logger.info(f"✅ Found {active_integrations} active Linear integrations")

            # Check OpenRouter API key
            from src.core.openrouter_client import get_api_key
            if not get_api_key():
                logger.warning("⚠️  OPENCOUNCIL_API not configured - AI analysis will be limited")
            else:
                logger.info("✅ OpenRouter API key configured")

        except Exception as e:
            logger.error(f"❌ Health check failed: {e}")
            raise
        finally:
            db.close()

    async def _periodic_health_check(self):
        """Run periodic health checks."""
        while not self.shutdown_requested:
            try:
                await asyncio.sleep(self.config.health_check_interval_minutes * 60)
                if not self.shutdown_requested:
                    await self._health_check()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Periodic health check failed: {e}")

    async def _send_error_alert(self, error: Exception):
        """Send email alert for critical errors."""
        if not self.config.admin_email or not EMAIL_AVAILABLE:
            logger.warning(f"📧 Email alert requested but email not configured or available")
            return

        try:
            subject = "🚨 Linear Ticket Polling Service Error Alert"

            # For now, just use send_test_email with the error info in the subject
            # TODO: Create a more flexible email function that accepts custom body
            error_subject = f"{subject} - {str(error)[:50]}..."

            # Since we don't have a generic send_email function yet, just log the alert
            logger.error(f"📧 ALERT for {self.config.admin_email}: {subject}")
            logger.error(f"   Error: {str(error)}")
            logger.error(f"   Consecutive Errors: {self.consecutive_errors}")
            logger.error(f"   Last Successful Cycle: {self.last_successful_cycle or 'Never'}")

            # Optionally try to send a basic test email as notification
            try:
                send_test_email(self.config.admin_email, error_subject)
                logger.info(f"📧 Error notification email sent to {self.config.admin_email}")
            except Exception as email_error:
                logger.warning(f"📧 Could not send email notification: {email_error}")

        except Exception as e:
            logger.error(f"❌ Failed to send error alert: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Get current service status."""
        uptime = None
        if self.stats["uptime_start"]:
            uptime = (datetime.utcnow() - self.stats["uptime_start"]).total_seconds()

        return {
            "running": self.running,
            "shutdown_requested": self.shutdown_requested,
            "cycle_count": self.cycle_count,
            "consecutive_errors": self.consecutive_errors,
            "last_successful_cycle": self.last_successful_cycle.isoformat() if self.last_successful_cycle else None,
            "uptime_seconds": uptime,
            "stats": self.stats.copy(),
            "config": {
                "polling_interval_minutes": self.config.polling_interval_minutes,
                "max_tickets_per_integration": self.config.max_tickets_per_integration,
                "max_concurrent_integrations": self.config.max_concurrent_integrations
            }
        }

    async def graceful_shutdown(self):
        """Gracefully shutdown the service."""
        logger.info("🛑 Initiating graceful shutdown...")

        self.shutdown_requested = True

        # Wait for current cycle to complete (with timeout)
        timeout = self.config.shutdown_timeout_seconds
        start_time = datetime.utcnow()

        while self.running and (datetime.utcnow() - start_time).total_seconds() < timeout:
            await asyncio.sleep(1)

        if self.running:
            logger.warning(f"⚠️  Shutdown timeout reached ({timeout}s), forcing stop")
        else:
            logger.info("✅ Graceful shutdown completed")


class LinearPollingServiceManager:
    """Manager for the Linear polling service."""

    def __init__(self):
        self.service: Optional[LinearPollingService] = None
        self.service_task: Optional[asyncio.Task] = None

    async def start_service(self, config: Optional[PollingConfig] = None):
        """Start the polling service."""
        if self.service and self.service.running:
            raise ValueError("Service is already running")

        self.service = LinearPollingService(config)
        self.service_task = asyncio.create_task(self.service.start())

        logger.info("🚀 Linear Polling Service started")

    async def stop_service(self):
        """Stop the polling service."""
        if not self.service:
            return

        await self.service.graceful_shutdown()

        if self.service_task and not self.service_task.done():
            self.service_task.cancel()
            try:
                await self.service_task
            except asyncio.CancelledError:
                pass

        self.service = None
        self.service_task = None

        logger.info("🛑 Linear Polling Service stopped")

    def get_status(self) -> Dict[str, Any]:
        """Get service status."""
        if not self.service:
            return {"running": False, "service": None}

        return {
            "running": True,
            "service": self.service.get_status()
        }

    async def reload_config(self, config: PollingConfig):
        """Reload service with new configuration."""
        logger.info("🔄 Reloading service configuration...")

        was_running = self.service and self.service.running

        if was_running:
            await self.stop_service()

        if was_running:
            await self.start_service(config)


# Global service manager instance
polling_service_manager = LinearPollingServiceManager()


async def main():
    """Main entry point for running the service standalone."""
    # Load configuration from environment
    config = PollingConfig(
        polling_interval_minutes=int(os.getenv("LINEAR_POLLING_INTERVAL_MINUTES", "15")),
        max_tickets_per_integration=int(os.getenv("LINEAR_MAX_TICKETS_PER_INTEGRATION", "5")),
        max_concurrent_integrations=int(os.getenv("LINEAR_MAX_CONCURRENT_INTEGRATIONS", "3")),
        max_consecutive_errors=int(os.getenv("LINEAR_MAX_CONSECUTIVE_ERRORS", "5")),
        error_backoff_minutes=int(os.getenv("LINEAR_ERROR_BACKOFF_MINUTES", "30")),
        enable_email_alerts=os.getenv("LINEAR_ENABLE_EMAIL_ALERTS", "true").lower() == "true",
        admin_email=os.getenv("LINEAR_ADMIN_EMAIL"),
        shutdown_timeout_seconds=int(os.getenv("LINEAR_SHUTDOWN_TIMEOUT_SECONDS", "300")),
        health_check_interval_minutes=int(os.getenv("LINEAR_HEALTH_CHECK_INTERVAL_MINUTES", "60"))
    )

    try:
        await polling_service_manager.start_service(config)

        # Keep the service running
        while polling_service_manager.service and polling_service_manager.service.running:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("🛑 Received keyboard interrupt")
    except Exception as e:
        logger.error(f"❌ Service error: {e}")
    finally:
        await polling_service_manager.stop_service()


if __name__ == "__main__":
    asyncio.run(main())