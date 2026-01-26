'use server';

import { API_BASE } from '@/lib/config';
import { fetchWithAuth } from '@/lib/api-client';

export interface SystemStats {
    // Original metrics
    system_status: string;
    system_status_color: string;
    total_incidents: number;
    open_incidents: number;
    investigating_incidents: number;
    healing_incidents: number;
    resolved_incidents: number;
    failed_incidents: number;
    critical_incidents: number;
    high_incidents: number;
    medium_incidents: number;
    low_incidents: number;
    active_incidents: number;
    total_services: number;
    unhealthy_services: number;
    error_logs_count: number;

    // Incident Resolution Metrics
    mttr_seconds: number;
    auto_fix_success_rate: number;
    total_auto_fix_attempts: number;
    successful_prs: number;
    pr_stats: {
        total: number;
        draft: number;
        ready: number;
        pending_qa: number;
        avg_review_time_seconds: number;
    };

    // Agent Activity
    agent_stats: {
        total_agents: number;
        available: number;
        working: number;
        idle: number;
        current_tasks: Array<{
            agent_name: string;
            task: string;
        }>;
        total_completed_tasks: number;
    };

    // Linear Resolution
    linear_stats: {
        total_attempts: number;
        claimed: number;
        analyzing: number;
        implementing: number;
        testing: number;
        completed: number;
        failed: number;
        success_rate: number;
        avg_resolution_time_seconds: number;
        avg_confidence_score: number;
    };

    // Time-Based Trends
    trends: {
        incidents_7d: number;
        incidents_30d: number;
        errors_7d: number;
        errors_30d: number;
        daily_incidents: Array<{
            date: string;
            count: number;
        }>;
    };

    // Source Breakdown
    incidents_by_source: Record<string, number>;
    most_affected_services: Array<{
        service: string;
        incident_count: number;
    }>;
    error_distribution_by_service: Array<{
        service: string;
        error_count: number;
    }>;
}

export async function getSystemStats(): Promise<SystemStats | null> {
    try {
        const response = await fetchWithAuth(`${API_BASE}/stats`, {
            cache: 'no-store' // Ensure we always get fresh data
        });

        if (!response.ok) {
            console.error(
                'Failed to fetch system stats:',
                response.status,
                await response.text()
            );
            return null;
        }

        return await response.json();
    } catch (error) {
        // Handle explicit timeout errors gracefully
        if (
            error instanceof Error &&
            error.message.includes('Request timeout')
        ) {
            console.warn('⚠️ System stats fetch timed out - returning null');
        } else {
            console.error('Error fetching system stats:', error);
        }
        return null;
    }
}
