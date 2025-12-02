import {
    Table,
    TableBody,
    TableCaption,
    TableCell,
    TableHead,
    TableHeader,
    TableRow
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import Link from 'next/link';

export interface Incident {
    id: number;
    title: string;
    status: string;
    severity: string;
    service_name: string;
    source: string;
    created_at: string;
    last_seen_at: string;
    root_cause: string | null;
    action_taken: string | null;
    metadata_json?: unknown;
}

export function IncidentTable({ incidents }: { incidents: Incident[] }) {
    return (
        <Table>
            <TableCaption>A list of recent incidents.</TableCaption>
            <TableHeader>
                <TableRow>
                    <TableHead className="w-[100px]">ID</TableHead>
                    <TableHead>Title</TableHead>
                    <TableHead>Service</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Severity</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Last Seen</TableHead>
                    <TableHead className="text-right">View</TableHead>
                </TableRow>
            </TableHeader>
            <TableBody>
                {incidents.map((incident) => (
                    <TableRow key={incident.id}>
                        <TableCell className="font-medium">
                            {incident.id}
                        </TableCell>
                        <TableCell>{incident.title}</TableCell>
                        <TableCell>{incident.service_name}</TableCell>
                        <TableCell className="uppercase text-xs font-mono">
                            {incident.source || 'N/A'}
                        </TableCell>
                        <TableCell>
                            <Badge
                                variant={
                                    incident.severity === 'CRITICAL'
                                        ? 'destructive'
                                        : 'outline'
                                }
                            >
                                {incident.severity}
                            </Badge>
                        </TableCell>
                        <TableCell>
                            <Badge
                                variant={
                                    incident.status === 'RESOLVED'
                                        ? 'default'
                                        : 'secondary'
                                }
                            >
                                {incident.status}
                            </Badge>
                        </TableCell>
                        <TableCell className="text-muted-foreground text-sm">
                            {new Date(incident.last_seen_at).toLocaleString()}
                        </TableCell>
                        <TableCell className="text-right">
                            <Link href={`/incidents/${incident.id}`}>
                                <Button variant="ghost" size="sm">
                                    Details
                                </Button>
                            </Link>
                        </TableCell>
                    </TableRow>
                ))}
            </TableBody>
        </Table>
    );
}
