import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

interface Incident {
  id: number
  title: string
  status: string
  severity: string
  service: string
  createdAt: string
  rootCause: string | null
  actionTaken: string | null
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
          <TableHead>Severity</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Action Taken</TableHead>
          <TableHead className="text-right">View</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {incidents.map((incident) => (
          <TableRow key={incident.id}>
            <TableCell className="font-medium">{incident.id}</TableCell>
            <TableCell>{incident.title}</TableCell>
            <TableCell>{incident.service}</TableCell>
            <TableCell>
              <Badge variant={incident.severity === "CRITICAL" ? "destructive" : "outline"}>
                {incident.severity}
              </Badge>
            </TableCell>
            <TableCell>
              <Badge variant={incident.status === "RESOLVED" ? "default" : "secondary"}>
                {incident.status}
              </Badge>
            </TableCell>
            <TableCell>{incident.actionTaken || "-"}</TableCell>
            <TableCell className="text-right">
              <Button variant="ghost" size="sm">Details</Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
