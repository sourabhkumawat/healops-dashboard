export type LogEntry = {
    id: number;
    service_name: string;
    severity: string;
    level: string;
    message: string;
    source: string;
    timestamp: string;
    metadata?: Record<string, unknown> | null;
    integration_id: number | null;
};

export interface LogFilters {
    limit?: number;
    level?: string;
    service?: string;
    search?: string;
    eventType?: 'logs' | 'spans';
}
