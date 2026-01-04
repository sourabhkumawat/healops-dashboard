export interface Incident {
    id: number;
    title: string;
    description?: string;
    status: 'OPEN' | 'INVESTIGATING' | 'HEALING' | 'RESOLVED' | 'FAILED';
    severity: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';
    service_name: string;
    source: string;
    created_at: string;
    last_seen_at: string;
    root_cause: string | null;
    action_taken: string | null;
    action_result?: {
        pr_url?: string;
        pr_number?: number;
        pr_files_changed?: string[];
        status?: string;
        error?: string;
    };
    metadata_json?: Record<string, unknown>;
    user_id?: number;
}

export interface IncidentStats {
    total: number;
    open: number;
    investigating: number;
    healing: number;
    resolved: number;
    failed: number;
}
