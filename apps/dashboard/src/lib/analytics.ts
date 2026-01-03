/**
 * Analytics and error tracking helper
 * Integrates with HealOps SDK for error tracking and provides analytics functions
 */

interface AnalyticsEvent {
    event: string;
    properties?: Record<string, any>;
}

// Simple analytics tracker that works with or without HealOps SDK
class AnalyticsTracker {
    private isEnabled: boolean = true;
    private events: AnalyticsEvent[] = [];

    constructor() {
        // Check if we're in browser environment
        if (typeof window === 'undefined') {
            this.isEnabled = false;
            return;
        }

        // Check if HealOps SDK is available
        if ((window as any).healops) {
            console.debug('HealOps SDK detected, using for analytics');
        } else {
            console.debug(
                'HealOps SDK not detected, using console logging for analytics'
            );
        }
    }

    track(event: string, properties?: Record<string, any>): void {
        if (!this.isEnabled) return;

        const analyticsEvent: AnalyticsEvent = {
            event,
            properties: {
                ...properties,
                timestamp: new Date().toISOString(),
                url:
                    typeof window !== 'undefined'
                        ? window.location.href
                        : undefined
            }
        };

        // Try to use HealOps SDK if available
        if (typeof window !== 'undefined' && (window as any).healops) {
            try {
                (window as any).healops.track(event, properties);
            } catch (error) {
                console.debug(
                    'HealOps SDK tracking failed, falling back to console:',
                    error
                );
                console.log('📊 Analytics:', analyticsEvent);
            }
        } else {
            // Fallback to console logging in development
            // Check if NODE_ENV is available (Next.js makes it available)
            const isDev =
                typeof process !== 'undefined' &&
                process.env &&
                process.env.NODE_ENV === 'development';
            if (isDev) {
                console.log('📊 Analytics:', analyticsEvent);
            }
        }

        // Store event for potential batch sending
        this.events.push(analyticsEvent);

        // Keep only last 100 events in memory
        if (this.events.length > 100) {
            this.events.shift();
        }
    }

    error(error: Error, context?: Record<string, any>): void {
        if (!this.isEnabled) return;

        // Try to use HealOps SDK for error tracking
        if (typeof window !== 'undefined' && (window as any).healops) {
            try {
                (window as any).healops.captureException(error, {
                    extra: context,
                    tags: {
                        component: 'dashboard',
                        environment:
                            (typeof process !== 'undefined' &&
                                process.env &&
                                process.env.NODE_ENV) ||
                            'production'
                    }
                });
            } catch (e) {
                console.error('HealOps SDK error tracking failed:', e);
                console.error('Original error:', error, context);
            }
        } else {
            // Fallback error logging
            console.error('Error tracked:', error, context);
        }
    }

    getEvents(): AnalyticsEvent[] {
        return [...this.events];
    }

    clearEvents(): void {
        this.events = [];
    }
}

// Export singleton instance
export const analytics = new AnalyticsTracker();

// Helper functions for common events
export const trackIncidentAnalysis = (
    incidentId: number,
    success: boolean,
    pollCount?: number,
    durationMs?: number
) => {
    analytics.track('incident_analysis_completed', {
        incident_id: incidentId,
        success,
        poll_count: pollCount,
        duration_ms: durationMs
    });
};

export const trackIncidentFetchError = (
    incidentId: number,
    error: Error | string
) => {
    analytics.track('incident_fetch_error', {
        incident_id: incidentId,
        error: error instanceof Error ? error.message : String(error)
    });

    if (error instanceof Error) {
        analytics.error(error, { incident_id: incidentId });
    }
};

export const trackAnalysisRequest = (incidentId: number) => {
    analytics.track('incident_analysis_requested', {
        incident_id: incidentId
    });
};

export const trackPageView = (page: string) => {
    analytics.track('page_view', {
        page
    });
};
