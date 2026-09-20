"""Explicit real adapter facade. The public command set cannot inject event times or probabilities."""

from football_system.application.real_bridge_requests import REQUEST_TYPES
from football_system.domain.real_bridge import RealProspectiveRunV1, require
from football_system.domain.archive import canonical_json
from football_system.domain.services.prospective import render_packet_markdown


class RealProspectiveDecisionAdapterV1:
    def __init__(self,repository):
        self.repository=repository

    def execute(self,operation,request):
        require(operation in REQUEST_TYPES and type(request) is REQUEST_TYPES[operation],"CLOSED_REAL_COMMAND_REQUIRED")
        return self.repository.execute(operation,request)

    def packet_files(self,run_id):
        run=self.repository.load(run_id)
        require(type(run) is RealProspectiveRunV1 and run.status=="PREPARING" and run.packet is not None,"READY_REAL_RUN_REQUIRED")
        packet=self.repository.load(run.packet.artifact_id)
        snapshots=self.repository.packet_evidence(run)
        markdown=f"Provenance: {run.provenance}\n\n"+render_packet_markdown(run,packet,snapshots)
        return canonical_json(packet),markdown
