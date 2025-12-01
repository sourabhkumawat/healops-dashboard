'use client';

import { useEffect, useState } from 'react';
import { IncidentTable, Incident } from '@/components/incident-table';
import { Loader2 } from 'lucide-react';
import { getIncidents } from '@/actions/incidents';

export default function IncidentsPage() {
    const [incidents, setIncidents] = useState<Incident[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchIncidents = async () => {
            try {
                const data = await getIncidents();
                setIncidents(data);
            } catch (error) {
                console.error('Failed to fetch incidents:', error);
            } finally {
                setLoading(false);
            }
        };

        fetchIncidents();

        // Poll every 10 seconds
        const interval = setInterval(fetchIncidents, 10000);
        return () => clearInterval(interval);
    }, []);

    return (
        <div className="flex-1 space-y-4">
            <div className="flex items-center justify-between space-y-2">
                <h2 className="text-3xl font-bold tracking-tight">Incidents</h2>
            </div>
            <div className="hidden h-full flex-1 flex-col space-y-8 md:flex">
                {loading && incidents.length === 0 ? (
                    <div className="flex h-[400px] items-center justify-center">
                        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                    </div>
                ) : (
                    <IncidentTable incidents={incidents} />
                )}
            </div>
        </div>
    );
}
