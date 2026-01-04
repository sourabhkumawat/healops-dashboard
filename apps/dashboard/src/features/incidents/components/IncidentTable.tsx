import {
    Table,
    TableBody,
    TableCaption,
    TableCell,
    TableHead,
    TableHeader,
    TableRow
} from '@healops/ui';
import { Badge } from '@healops/ui';
import { Button } from '@healops/ui';
import Link from 'next/link';
import { Incident } from '../types';

interface IncidentTableProps {
    incidents: Incident[];
    fullHeight?: boolean;
}

export function IncidentTable({
    incidents,
    fullHeight = false
}: IncidentTableProps) {
    return (
        <div
            className={`${
                fullHeight ? 'h-full' : 'h-[400px]'
            } border rounded-md overflow-hidden flex flex-col`}
        >
            <div className="overflow-y-auto overflow-x-auto flex-1">
                <Table>
                    <TableCaption className="sr-only">
                        A list of recent incidents.
                    </TableCaption>
                    <TableHeader className="sticky top-0 bg-background z-10 border-b">
                        <TableRow>
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
                                    {new Date(
                                        incident.last_seen_at
                                    ).toLocaleString()}
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
            </div>
        </div>
    );
}
