import { IncidentTable } from "@/components/incident-table";
import { mockIncidents } from "@/lib/mock-data";

export default function IncidentsPage() {
  return (
    <div className="flex-1 space-y-4">
      <div className="flex items-center justify-between space-y-2">
        <h2 className="text-3xl font-bold tracking-tight">Incidents</h2>
      </div>
      <div className="hidden h-full flex-1 flex-col space-y-8 md:flex">
        <IncidentTable incidents={mockIncidents} />
      </div>
    </div>
  );
}
